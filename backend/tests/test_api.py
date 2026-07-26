from __future__ import annotations

import asyncio
from io import BytesIO
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from docx import Document


def test_openai_compatible_provider_retries_v1_after_root_404(monkeypatch):
    from backend.app.services.llm import OpenAICompatibleProvider

    calls = []

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, **_kwargs):
            calls.append(url)
            request = httpx.Request("POST", url)
            if url.endswith("/v1/chat/completions"):
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": "OK"}}]},
                )
            return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = OpenAICompatibleProvider("https://api.example.com", "secret")
    response = asyncio.run(
        provider.chat(
            [{"role": "user", "content": "测试"}],
            model="example-model",
            temperature=0,
        )
    )

    assert response.content == "OK"
    assert calls == [
        "https://api.example.com/chat/completions",
        "https://api.example.com/v1/chat/completions",
    ]


def test_openai_compatible_provider_retries_transient_http_failure(monkeypatch):
    from backend.app.services.llm import OpenAICompatibleProvider

    calls = 0

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, **_kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if calls < 3:
                return httpx.Response(503, request=request, text="busy")
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "recovered"}}]},
            )

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    response = asyncio.run(
        OpenAICompatibleProvider(
            "https://api.example.com/v1",
            "secret",
        ).chat(
            [{"role": "user", "content": "测试重试"}],
            model="example-model",
            temperature=0,
        )
    )

    assert response.content == "recovered"
    assert calls == 3


def test_siliconflow_provider_uses_v1_stream_and_reassembles_output(monkeypatch):
    from backend.app.services.llm import OpenAICompatibleProvider

    calls = []

    class StreamResponse:
        is_error = False
        headers = {"content-type": "text/event-stream"}

        async def aiter_lines(self):
            for line in [
                'data: {"choices":[{"delta":{"content":"前沿"}}]}',
                'data: {"choices":[{"delta":{"content":"资料"}}]}',
                'data: {"choices":[],"usage":{"total_tokens":12}}',
                "data: [DONE]",
            ]:
                yield line

    class StreamContext:
        async def __aenter__(self):
            return StreamResponse()

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return StreamContext()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    response = asyncio.run(
        OpenAICompatibleProvider(
            "https://api.siliconflow.cn",
            "secret",
            request_options={"enable_thinking": False},
        ).chat(
            [{"role": "user", "content": "检索资料"}],
            model="Pro/zai-org/GLM-5.1",
            temperature=0.3,
        )
    )

    assert response.content == "前沿资料"
    assert response.tokens == 12
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert calls[0]["json"]["stream"] is True
    assert calls[0]["json"]["enable_thinking"] is False


def test_provider_does_not_retry_read_timeout_that_may_be_billable(monkeypatch):
    from backend.app.services.llm import OpenAICompatibleProvider

    calls = 0

    class TimeoutContext:
        async def __aenter__(self):
            raise httpx.ReadTimeout("")

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return TimeoutContext()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    provider = OpenAICompatibleProvider(
        "https://api.siliconflow.cn/v1",
        "secret",
        request_options={"_retry_attempts": 3},
    )
    with pytest.raises(RuntimeError, match="ReadTimeout.*避免重复计费"):
        asyncio.run(
            provider.chat(
                [{"role": "user", "content": "长文生成"}],
                model="Pro/zai-org/GLM-5.1",
                temperature=0.3,
            )
        )

    assert calls == 1


def test_teaching_plan_uses_structured_model_script():
    from backend.app.services.llm import LLMResponse
    from backend.app.services.teaching import teaching_service

    class FakeProvider:
        async def chat(self, *_args, **_kwargs):
            return LLMResponse(
                content=json.dumps(
                    {
                        "sections": [
                            {
                                "section_index": 0,
                                "narration": "先定义映射，再对雅可比行列式逐步求导。",
                                "focus_phrases": ["雅可比行列式"],
                                "formulas": ["J=\\partial(x,y)/\\partial(\\xi,\\eta)"],
                                "board_steps": ["J=x_\\xi y_\\eta-x_\\eta y_\\xi", "要求 J>0"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    agent = SimpleNamespace(
        name="网格研究 Agent",
        system_prompt="你是严谨的网格研究教师。",
        model="custom-model",
        temperature=0.2,
    )
    result = asyncio.run(
        teaching_service.create_plan(
            "# 雅可比质量\n\n映射公式为 $J=\\partial(x,y)/\\partial(\\xi,\\eta)$。",
            agent,
            FakeProvider(),
        )
    )

    assert result["mode"] == "model"
    assert result["sections"][0]["focus_phrases"] == ["雅可比行列式"]
    assert result["sections"][0]["board_steps"][-1] == "要求 J>0"


def test_web_research_relevance_rejects_structural_engineering_noise():
    from backend.app.services.web_research import web_research_service

    task = "帮我总结关于2D结构化网格质量评估的综述"
    results = [
        {
            "title": "A scale-generalizable method for 2D structured mesh quality evaluation",
            "url": "https://doi.org/10.1000/relevant",
            "doi": "10.1000/relevant",
            "source": "Crossref",
            "description": "",
        },
        {
            "title": "钢结构工程中的质量风险评估与防控措施",
            "url": "https://example.org/steel",
            "source": "Bing Web Search",
            "description": "",
        },
        {
            "title": "Local Mesh Enrichment for a Block Structured 3D Euler Solver",
            "url": "https://doi.org/10.1000/3d",
            "source": "Crossref",
            "description": "",
        },
    ]

    ranked = web_research_service._rank_results(task, results)

    assert [item["title"] for item in ranked] == [results[0]["title"]]
    assert ranked[0]["matched_concepts"] == [
        "dimension",
        "structured_mesh",
        "mesh",
        "quality",
    ]
    assert web_research_service.query_variants(task)[0] == (
        "2D structured grid mesh quality assessment"
    )


def test_university_investigation_triggers_targeted_web_research():
    from backend.app.services.web_research import web_research_service

    task = "北京工商大学调查"
    assert web_research_service.should_research(task)
    assert web_research_service.institution_name(task) == "北京工商大学"
    assert web_research_service.query_variants(task) == [
        '"北京工商大学" 官网 招生',
        '"北京工商大学" 师资 学科专业',
        '"北京工商大学" 就业质量报告 录取分数',
        '"北京工商大学" 校园生活 宿舍',
    ]

    official = {
        "title": "北京工商大学本科招生网",
        "url": "https://zsb.btbu.edu.cn/",
        "source": "Bing Web Search",
        "description": "北京工商大学招生信息",
    }
    noise = {
        "title": "北京其他高校校园资讯",
        "url": "https://example.com/other",
        "source": "Bing Web Search",
        "description": "与目标学校无关",
    }

    ranked = web_research_service._rank_results(task, [noise, official])

    assert [item["title"] for item in ranked] == [official["title"]]
    assert ranked[0]["matched_concepts"] == ["institution"]


def test_general_search_expands_to_comprehensive_queries():
    from backend.app.services.web_research import web_research_service

    assert web_research_service.should_research("了解量子计算产业")
    assert web_research_service.research_mode("北京航空航天大学调查") == "web"
    assert web_research_service.research_mode("二维结构化网格文献综述") == "academic"
    assert web_research_service.query_variants("查询量子计算产业") == [
        "量子计算产业",
        "量子计算产业 官方 权威来源",
        "量子计算产业 最新 数据 报告",
        "量子计算产业 背景 现状",
    ]


def test_360_search_parser_prefers_direct_result_url():
    from backend.app.services.web_research import web_research_service

    body = """
    <h3 class="res-title"><a href="https://www.so.com/link?m=opaque"
      data-mdurl="https://zsb.btbu.edu.cn/index.htm" target="_blank">
      <em>北京工商大学</em>本科招生网</a></h3>
    """

    results = web_research_service._parse_360_results(body)

    assert results[0]["title"] == "北京工商大学 本科招生网"
    assert results[0]["url"] == "https://zsb.btbu.edu.cn/index.htm"
    assert results[0]["credibility"]["level"] == "较高"


def test_health_and_seeded_overview(client):
    assert client.get("/health").json()["status"] == "healthy"
    overview = client.get("/api/overview").json()
    assert overview["counts"]["agents"] == 9
    assert overview["counts"]["workflows"] == 1
    assert overview["counts"]["knowledge_bases"] == 1
    agents = client.get("/api/agents").json()
    assert sum(agent["status"] == "active" for agent in agents) == 5
    assert sum(agent["status"] == "candidate" for agent in agents) == 2
    assert sum(agent["status"] == "archived" for agent in agents) == 2
    catalog = {
        agent["slug"]: agent
        for agent in agents
        if agent["slug"]
        in {
            "knowledge-curator",
            "data-insight-reporter",
            "requirement-designer",
            "multi-agent-coordinator",
            "legacy-material-extractor",
            "legacy-format-proofreader",
        }
    }
    assert len(catalog) == 6
    for agent in catalog.values():
        assert "exec" in json.loads(agent["tools_json"])
        assert json.loads(agent["skills_json"])
        assert json.loads(agent["permissions_json"])["mcp_extensions"]


def test_windows_tauri_origin_is_allowed(client):
    response = client.get(
        "/health",
        headers={"Origin": "http://tauri.localhost"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_demo_agent_and_multi_agent_workflow(client):
    agents = client.get("/api/agents").json()
    planner = next(item for item in agents if item["slug"] == "planner")
    run = client.post(
        f"/api/agents/{planner['id']}/run",
        json={"input": "规划一个可信的学科研究任务", "context": {}},
    ).json()
    assert run["status"] == "completed"
    assert "AI 生成内容" not in run["output_text"] or run["output_text"]

    workflow = client.get("/api/workflows").json()[0]
    workflow_run = client.post(
        f"/api/workflows/{workflow['id']}/run",
        json={"input": {"task": "建立可追溯的学科问答方案"}},
    ).json()
    assert workflow_run["status"] == "completed"
    assert "answer" in workflow_run["output_json"]
    assert len(__import__("json").loads(workflow_run["trace_json"])) == 5


def test_approval_policy_and_tool_execution(client):
    policy = next(
        item for item in client.get("/api/approval-policies").json() if item["name"] == "稳健默认"
    )
    response = client.post(
        "/api/tools/run",
        json={
            "tool": "write_file",
            "arguments": {"path": "tests/approved.txt", "content": "approved"},
            "policy_id": policy["id"],
        },
    ).json()
    assert response["status"] == "approval_required"
    decision = client.post(
        f"/api/approvals/{response['approval_id']}/decide",
        json={"approved": True, "decided_by": "pytest"},
    ).json()
    assert decision["status"] == "approved"
    read = client.post(
        "/api/tools/run",
        json={
            "tool": "read_file",
            "arguments": {"path": "tests/approved.txt"},
            "policy_id": policy["id"],
        },
    ).json()
    assert read["result"]["content"] == "approved"


def test_workspace_escape_is_blocked(client):
    response = client.post(
        "/api/tools/run",
        json={
            "tool": "read_file",
            "arguments": {"path": "../../outside.txt"},
            "permission_mode": "auto",
        },
    )
    assert response.status_code == 400
    assert "超出授权工作区" in response.json()["detail"]


def test_runtime_security_supports_workspace_and_per_turn_full_access(client, tmp_path):
    workspace = tmp_path / "safe-workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-visible-only-with-full-access", encoding="utf-8")
    configured = client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "custom",
            "workspace_roots": [str(workspace)],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )
    assert configured.status_code == 200
    assert configured.json()["workspace_roots"] == [str(workspace.resolve())]

    blocked = client.post(
        "/api/tools/run",
        json={
            "tool": "read_file",
            "arguments": {"path": str(outside)},
            "permission_mode": "auto",
            "security_profile": "default",
        },
    )
    allowed = client.post(
        "/api/tools/run",
        json={
            "tool": "read_file",
            "arguments": {"path": str(outside)},
            "permission_mode": "auto",
            "security_profile": "unrestricted_auto",
        },
    )
    assert blocked.status_code == 400
    assert allowed.status_code == 200
    assert allowed.json()["result"]["content"] == "outside-visible-only-with-full-access"

    workspace_profile_blocked = client.post(
        "/api/tools/run",
        json={
            "tool": "read_file",
            "arguments": {"path": str(outside)},
            "permission_mode": "auto",
            "security_profile": "workspace_auto",
        },
    )
    executed = client.post(
        "/api/tools/run",
        json={
            "tool": "exec",
            "arguments": {"command": "Write-Output exec-ready"},
            "permission_mode": "auto",
            "security_profile": "custom_auto",
        },
    )
    assert workspace_profile_blocked.status_code == 400
    assert executed.status_code == 200
    assert executed.json()["result"]["exit_code"] == 0
    assert "exec-ready" in executed.json()["result"]["stdout"]

    client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "workspace",
            "workspace_roots": ["data/workspace"],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )


def test_local_path_request_uses_local_tool_without_web_research(client, tmp_path):
    workspace = tmp_path / "local-intent"
    workspace.mkdir()
    (workspace / "课程安排.txt").write_text("周五进行项目验收", encoding="utf-8")
    client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "custom",
            "workspace_roots": [str(workspace)],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )
    created = client.post(
        "/api/agents",
        json={
            "name": "本地文件 Agent",
            "slug": "local-file-intent-agent",
            "description": "优先处理本地文件任务",
            "system_prompt": "你负责按照用户要求安全地访问本地目录和文件，并如实返回结果。",
            "provider": "demo",
            "model": "demo-model",
            "tools": ["list_directory", "read_file", "search_files", "web_research"],
            "permissions": {"tool_mode": "auto"},
        },
    ).json()
    run = client.post(
        f"/api/agents/{created['id']}/run",
        json={
            "input": f"帮我列出本地目录 {workspace}",
            "context": {"security_profile": "custom_auto"},
        },
    ).json()
    trace = json.loads(run["trace_json"])
    types = [item.get("type") for item in trace]
    assert run["status"] == "completed"
    assert "local_intent_detected" in types
    assert "web_search_started" not in types
    assert "课程安排.txt" in run["output_text"]

    client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "workspace",
            "workspace_roots": ["data/workspace"],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )


def test_local_request_planner_targets_a_named_desktop_file():
    from pathlib import Path

    from backend.app.services.tools import ToolRuntime

    planned = ToolRuntime.plan_local_request("帮我读取桌面上的 notes.txt")

    assert planned == {
        "tool": "read_file",
        "arguments": {"path": "桌面/notes.txt"},
    }
    assert ToolRuntime._known_folder("桌面/notes.txt") == Path.home() / "Desktop" / "notes.txt"


def test_conversation_resumes_after_manual_tool_approval(client, monkeypatch, tmp_path):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse

    class WriteAfterApprovalProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            if any(message.get("role") == "tool" for message in messages):
                return LLMResponse(content="The approved local write completed.", tokens=4)
            return LLMResponse(
                content="",
                tokens=3,
                tool_calls=[
                    {
                        "id": "write-after-approval",
                        "name": "write_file",
                        "arguments": {"path": "approved-result.txt", "content": "approved"},
                    }
                ],
            )

    monkeypatch.setattr(
        agents_service,
        "get_provider",
        lambda _provider: WriteAfterApprovalProvider(),
    )
    original_security = client.get("/api/security/runtime").json()
    workspace = tmp_path / "approval-workspace"
    workspace.mkdir()
    client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "custom",
            "workspace_roots": [str(workspace)],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )
    created = client.post(
        "/api/agents",
        json={
            "name": "Approval continuation agent",
            "slug": "approval-continuation-agent",
            "description": "Verifies that a paused conversation continues after approval.",
            "system_prompt": "Use the requested local tool and report its result.",
            "provider": "approval-continuation-test",
            "model": "test-model",
            "tools": ["write_file"],
            "permissions": {"tool_mode": "auto"},
        },
    ).json()
    conversation = client.post(
        f"/api/agents/{created['id']}/conversations",
        json={"title": "Approval continuation"},
    ).json()
    existing_ids = {item["id"] for item in client.get("/api/approvals").json()}

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            response_future = pool.submit(
                client.post,
                f"/api/conversations/{conversation['id']}/messages/stream",
                json={
                    "content": "Write the approved result into the local workspace.",
                    "security_profile": "custom_ask",
                },
            )
            approval = None
            for _attempt in range(100):
                pending = [
                    item
                    for item in client.get("/api/approvals").json()
                    if item["id"] not in existing_ids and item["status"] == "pending"
                ]
                if pending:
                    approval = pending[0]
                    break
                time.sleep(0.05)
            assert approval is not None

            decision = client.post(
                f"/api/approvals/{approval['id']}/decide",
                json={"approved": True, "decided_by": "pytest"},
            )
            assert decision.status_code == 200
            assert decision.json()["status"] == "approved"
            response = response_future.result(timeout=10)

        assert response.status_code == 200
        assert "approval_required" in response.text
        assert "approval_resolved" in response.text
        assert "The approved local write completed." in response.text
        assert (workspace / "approved-result.txt").read_text("utf-8") == "approved"
    finally:
        client.put(
            "/api/security/runtime",
            json={
                "filesystem_mode": original_security["filesystem_mode"],
                "workspace_roots": original_security["workspace_roots"],
                "command_mode": original_security["command_mode"],
                "block_critical_commands": original_security["block_critical_commands"],
            },
        )


def test_chinese_knowledge_search_returns_citations(client):
    results = client.post(
        "/api/knowledge/search",
        json={"query": "知识可信来源", "knowledge_base_ids": [], "top_k": 5},
    ).json()
    assert results
    assert all(item["citation"] for item in results)


def test_custom_model_endpoint_never_returns_secret(client):
    created = client.post(
        "/api/model-endpoints",
        json={
            "name": "测试接口",
            "provider_type": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "api_key": "super-secret",
            "default_model": "test-model",
            "headers": {"X-Tenant": "demo"},
            "request_options": {},
            "timeout_seconds": 5,
            "enabled": True,
        },
    ).json()
    assert created["has_api_key"] is True
    assert "api_key" not in created
    serialized = str(client.get("/api/model-endpoints").json())
    assert "super-secret" not in serialized


def test_custom_model_endpoint_agent_full_chain(client, monkeypatch):
    captured: list[dict] = []

    class MockAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={"data": [{"id": "mock-model"}]},
            )

        async def post(self, url, *, json, headers):
            captured.append({"url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {"message": {"content": "自定义模型全链路正常", "tool_calls": []}}
                    ],
                    "usage": {"total_tokens": 12},
                },
            )

    monkeypatch.setattr("backend.app.services.llm.httpx.AsyncClient", MockAsyncClient)
    endpoint = client.post(
        "/api/model-endpoints",
        json={
            "name": "全链路模型接口",
            "provider_type": "openai-compatible",
            "base_url": "https://mock-llm.example/v1/",
            "api_key": "chain-secret",
            "default_model": "mock-model",
            "headers": {"X-Tenant": "evoagent"},
            "request_options": {"response_format": {"type": "text"}},
            "timeout_seconds": 15,
            "enabled": True,
        },
    ).json()

    tested = client.post(f"/api/model-endpoints/{endpoint['id']}/test").json()
    assert tested["status"] == "healthy"
    agent = client.post(
        "/api/agents",
        json={
            "name": "自定义模型链路 Agent",
            "slug": "custom-model-chain",
            "system_prompt": "你是用于验证自定义模型接口完整链路的测试智能体。",
            "provider": "openai-compatible",
            "model_endpoint_id": endpoint["id"],
            "model": "ignored-by-endpoint",
            "tools": [],
            "permissions": {"tool_mode": "ask"},
        },
    ).json()
    run = client.post(
        f"/api/agents/{agent['id']}/run",
        json={"input": "执行自定义模型调用", "context": {}},
    ).json()

    assert run["status"] == "completed"
    assert run["output_text"] == "自定义模型全链路正常"
    assert run["token_usage"] == 12
    assert captured[-1]["url"] == "https://mock-llm.example/v1/chat/completions"
    assert captured[-1]["headers"]["Authorization"] == "Bearer chain-secret"
    assert captured[-1]["headers"]["X-Tenant"] == "evoagent"
    assert captured[-1]["json"]["model"] == "mock-model"
    assert captured[-1]["json"]["response_format"] == {"type": "text"}
    assert "chain-secret" not in str(client.get("/api/model-endpoints").json())


def test_image_model_endpoint_generates_only_when_needed(client, monkeypatch):
    captured: list[dict] = []

    class MockImageClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json, headers):
            captured.append({"url": url, "json": json, "headers": headers})
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "data": [
                        {
                            "b64_json": "aW1hZ2UtYnl0ZXM=",
                            "revised_prompt": "蓝色几何机器人",
                        }
                    ]
                },
            )

    monkeypatch.setattr("backend.app.services.llm.httpx.AsyncClient", MockImageClient)
    endpoint = client.post(
        "/api/model-endpoints",
        json={
            "name": "测试图片生成接口",
            "modality": "image",
            "provider_type": "openai-compatible",
            "base_url": "https://mock-image.example/v1",
            "api_key": "image-secret",
            "default_model": "image-model",
            "request_options": {"size": "512x512"},
            "enabled": True,
        },
    )
    assert endpoint.status_code == 201
    assert endpoint.json()["modality"] == "image"
    tested = client.post(
        f"/api/model-endpoints/{endpoint.json()['id']}/test"
    ).json()
    assert tested["status"] == "healthy"

    rejected = client.post(
        "/api/agents",
        json={
            "name": "错误模型绑定 Agent",
            "slug": "invalid-image-as-chat",
            "system_prompt": "验证图片接口不能被错误绑定为普通回答模型。",
            "model_endpoint_id": endpoint.json()["id"],
        },
    )
    assert rejected.status_code == 422

    agent = client.post(
        "/api/agents",
        json={
            "name": "图片回答 Agent",
            "slug": "image-answer-agent",
            "system_prompt": "根据用户目标回答，并在确有必要时配合图片生成能力。",
            "provider": "demo",
            "image_model_endpoint_id": endpoint.json()["id"],
            "tools": [],
            "skills": [],
        },
    )
    assert agent.status_code == 201
    run = client.post(
        f"/api/agents/{agent.json()['id']}/run",
        json={"input": "请生成一张蓝色几何机器人图片", "context": {}},
    ).json()

    assert run["status"] == "completed"
    assert "## 生成图片" in run["output_text"]
    assert "data:image/png;base64,aW1hZ2UtYnl0ZXM=" in run["output_text"]
    trace = json.loads(run["trace_json"])
    assert any(step["type"] == "image_generated" for step in trace)
    assert captured[-1]["json"]["model"] == "image-model"
    assert captured[-1]["json"]["size"] == "512x512"
    assert captured[-1]["headers"]["Authorization"] == "Bearer image-secret"
    generated_call_count = len(captured)
    plain_run = client.post(
        f"/api/agents/{agent.json()['id']}/run",
        json={"input": "请用一句话说明今天的任务安排", "context": {}},
    ).json()
    assert plain_run["status"] == "completed"
    assert "## 生成图片" not in plain_run["output_text"]
    assert len(captured) == generated_call_count


def test_math_question_automatically_uses_jsxgraph_skill(client):
    skill = next(
        item
        for item in client.get("/api/skills").json()
        if item["name"] == "jsxgraph-math-visualization"
    )
    assert skill["enabled"] is True
    agent = next(
        item for item in client.get("/api/agents").json() if item["slug"] == "planner"
    )
    run = client.post(
        f"/api/agents/{agent['id']}/run",
        json={
            "input": "求函数 y=x^2 在 x=1 处的导数和切线，并绘制对应图像",
            "context": {},
        },
    ).json()

    assert run["status"] == "completed"
    assert "$$f'(x)=2x$$" in run["output_text"]
    assert "```jsxgraph" in run["output_text"]
    assert '"type":"functiongraph"' in run["output_text"]
    trace = json.loads(run["trace_json"])
    context = next(step for step in trace if step["type"] == "context_ready")
    assert context["capabilities"]["math_visualization"] is True


def test_agent_conversation_streams_steps_and_keeps_history(client):
    planner = next(item for item in client.get("/api/agents").json() if item["slug"] == "planner")
    knowledge_before = {
        item["id"]: [
            document["id"]
            for document in client.get(
                f"/api/knowledge-bases/{item['id']}/documents"
            ).json()
        ]
        for item in client.get("/api/knowledge-bases").json()
    }
    conversation = client.post(
        f"/api/agents/{planner['id']}/conversations",
        json={"title": "新会话"},
    ).json()

    first = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "先制定研究问题"},
    )
    first_events = [
        json.loads(line.removeprefix("data: "))
        for line in first.text.splitlines()
        if line.startswith("data: ")
    ]
    first_steps = [event["step"] for event in first_events if event["type"] == "step"]
    assert first.status_code == 200
    first_step_types = [step["type"] for step in first_steps]
    assert first_step_types[:2] == ["stream_connected", "run_started"]
    assert first_step_types[-2:] == ["run_completed", "database_persisted"]
    assert first_step_types.index("model_response") < first_step_types.index(
        "generation_verification_started"
    )
    assert "generation_verified" in first_step_types
    assert [
        name
        for name in first_step_types
        if name
        in {
            "rag_query_condensed",
            "rag_query_rewrite_started",
            "rag_query_rewritten",
            "rag_hybrid_retrieval_started",
            "rag_hybrid_retrieval_completed",
            "rag_fusion_completed",
            "rag_rerank_started",
            "rag_rerank_completed",
            "rag_context_assembled",
        }
    ] == [
        "rag_query_condensed",
        "rag_query_rewrite_started",
        "rag_query_rewritten",
        "rag_hybrid_retrieval_started",
        "rag_hybrid_retrieval_completed",
        "rag_fusion_completed",
        "rag_rerank_started",
        "rag_rerank_completed",
        "rag_context_assembled",
    ]
    assert first_step_types.index("intent_detected") < first_step_types.index("context_ready")
    assert first_events[-1]["type"] == "done"
    assert next(step for step in first_steps if step["type"] == "context_ready")[
        "history_messages"
    ] == 0
    assert any(event["type"] == "assistant" for event in first_events)
    persistence_step = next(
        step for step in first_steps if step["type"] == "database_persisted"
    )
    run_id = next(step["run_id"] for step in first_steps if step["type"] == "run_started")
    assert persistence_step["run_id"] == run_id
    assert persistence_step["conversation_id"] == conversation["id"]
    assert persistence_step["storage"] == "business_database"
    assert persistence_step["knowledge_base_updated"] is False
    assert persistence_step["artifact_count"] == 0
    assert "agent_messages" in persistence_step["tables"]
    assert knowledge_before == {
        item["id"]: [
            document["id"]
            for document in client.get(
                f"/api/knowledge-bases/{item['id']}/documents"
            ).json()
        ]
        for item in client.get("/api/knowledge-bases").json()
    }

    second = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "继续补充验证标准"},
    )
    second_events = [
        json.loads(line.removeprefix("data: "))
        for line in second.text.splitlines()
        if line.startswith("data: ")
    ]
    context_step = next(
        event["step"]
        for event in second_events
        if event["type"] == "step" and event["step"]["type"] == "context_ready"
    )
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()

    assert context_step["history_messages"] == 2
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert "run_completed" in messages[-1]["trace_json"]


def test_agent_output_persists_to_database_without_knowledge_archive(client):
    agent = client.post(
        "/api/agents",
        json={
            "name": "后台归档测试 Agent",
            "slug": "background-archive-test",
            "system_prompt": "完成用户任务并给出简洁结果。",
            "provider": "demo",
            "model": "demo-model",
            "tools": [],
            "skills": [],
            "knowledge_bases": [],
            "permissions": {},
        },
    ).json()
    conversation = client.post(
        f"/api/agents/{agent['id']}/conversations",
        json={"title": "后台任务归档"},
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "生成一条可检索的任务成果"},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    persistence_step = next(
        event["step"]
        for event in events
        if event["type"] == "step" and event["step"]["type"] == "database_persisted"
    )

    assert response.status_code == 200
    assert persistence_step["storage"] == "business_database"
    assert persistence_step["knowledge_base_updated"] is False
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[-1]["id"] == persistence_step["message_id"]
    assert messages[-1]["content"]
    assert all(
        item["name"] != "Agent 任务成果"
        for item in client.get("/api/knowledge-bases").json()
    )


def test_multiple_agents_can_complete_conversations_concurrently(client):
    agents = {
        item["slug"]: item
        for item in client.get("/api/agents").json()
        if item["slug"] in {"planner", "researcher"}
    }
    conversations = [
        client.post(
            f"/api/agents/{agents[slug]['id']}/conversations",
            json={"title": f"{slug} 并发测试"},
        ).json()
        for slug in ("planner", "researcher")
    ]

    def run_conversation(conversation):
        return client.post(
            f"/api/conversations/{conversation['id']}/messages/stream",
            json={"content": f"完成并发任务 {conversation['id'][:8]}"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run_conversation, conversations))

    for conversation, response in zip(conversations, responses, strict=True):
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        step_types = [
            event["step"]["type"]
            for event in events
            if event["type"] == "step"
        ]
        messages = client.get(
            f"/api/conversations/{conversation['id']}/messages"
        ).json()
        assert response.status_code == 200
        assert events[-1]["type"] == "done"
        assert "run_completed" in step_types
        assert "database_persisted" in step_types
        assert "knowledge_archived" not in step_types
        assert [item["role"] for item in messages] == ["user", "assistant"]


def test_research_trace_survives_refresh_and_creates_database_artifact(
    client, monkeypatch
):
    from backend.app.services.web_research import web_research_service

    async def fake_collect(task, on_event):
        await on_event({"type": "research_planning", "queries": ["2D mesh quality"]})
        await on_event({"type": "web_search_started", "query": "2D mesh quality"})
        await on_event(
            {
                "type": "web_search_results",
                "query": "2D mesh quality",
                "count": 1,
                "results": [{"title": "Mesh quality paper", "url": "https://example.org/paper"}],
            }
        )
        await on_event(
            {
                "type": "web_page_fetched",
                "index": 1,
                "title": "Mesh quality paper",
                "url": "https://example.org/paper",
                "status": "fetched",
                "content_excerpt": "A structured mesh quality metric.",
            }
        )
        return [
            {
                "title": "Mesh quality paper",
                "url": "https://example.org/paper",
                "source": "Crossref",
                "content": "A structured mesh quality metric.",
            }
        ]

    monkeypatch.setattr(web_research_service, "collect", fake_collect)
    planner = next(item for item in client.get("/api/agents").json() if item["slug"] == "planner")
    conversation = client.post(
        f"/api/agents/{planner['id']}/conversations", json={"title": "刷新恢复验证"}
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "帮我总结关于2D结构化网格质量评估的综述"},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    types = [
        event["step"]["type"] if event["type"] == "step" else event["type"]
        for event in events
    ]
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    artifacts = client.get(f"/api/conversations/{conversation['id']}/artifacts").json()

    assert {"web_search_started", "web_page_fetched", "quality_review_started", "artifact_created"} <= set(types)
    assert messages[0]["run_id"]
    assert messages[0]["run_status"] == "completed"
    assert "web_page_fetched" in messages[0]["run_trace_json"]
    assert messages[-1]["run_status"] == "completed"
    assert len(artifacts) == 1
    assert artifacts[0]["relative_path"].startswith("database://agent-artifacts/")
    assert artifacts[0]["storage"] == "business_database"
    assert artifacts[0]["format"] == "MARKDOWN"
    assert artifacts[0]["content_characters"] == len(artifacts[0]["content"])
    assert "https://example.org/paper" in artifacts[0]["content"]
    assert "Google Scholar" in artifacts[0]["content"]

    teaching = client.post(
        f"/api/conversations/{conversation['id']}/teaching-plan",
        json={"artifact_id": artifacts[0]["id"]},
    )
    assert teaching.status_code == 200
    teaching_plan = teaching.json()
    assert teaching_plan["generated_by"] == planner["name"]
    assert teaching_plan["mode"] in {"model", "fallback"}
    assert teaching_plan["sections"]
    assert "narration" in teaching_plan["sections"][0]
    assert "board_steps" in teaching_plan["sections"][0]

    selected_teaching = client.post(
        f"/api/conversations/{conversation['id']}/teaching-plan",
        json={"artifact_id": artifacts[0]["id"], "section_indices": [0]},
    ).json()
    assert {item["section_index"] for item in selected_teaching["sections"]} == {0}

    local_speech = client.post(
        f"/api/conversations/{conversation['id']}/classroom-speech",
        json={"input": "大家注意这个关键指标。", "voice": "claire", "style": "natural"},
    )
    assert local_speech.status_code == 400

    reviewed = client.post(
        f"/api/conversations/{conversation['id']}/source-reviews",
        json={
            "run_id": messages[-1]["run_id"],
            "url": "https://example.org/paper",
            "title": "Mesh quality paper",
            "decision": "confirmed",
            "credibility": {"score": 82, "level": "较高"},
        },
    )
    reviews = client.get(
        f"/api/conversations/{conversation['id']}/source-reviews"
    ).json()
    assert reviewed.status_code == 200
    assert reviews[0]["decision"] == "confirmed"
    assert '"score": 82' in reviews[0]["credibility_json"]


def test_visual_workflow_positions_persist_and_graph_runs(client):
    planner = next(item for item in client.get("/api/agents").json() if item["slug"] == "planner")
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入", "position": {"x": 40, "y": 180}},
            {
                "id": "agent_visual",
                "type": "agent",
                "label": "规划 Agent",
                "position": {"x": 280, "y": 180},
                "config": {"agent_id": planner["id"], "input": "{{input.task}}"},
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "position": {"x": 520, "y": 180},
                "config": {"value": {"result": "{{nodes.agent_visual.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "agent_visual"},
            {"source": "agent_visual", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "可视化画板回归", "description": "验证坐标与连线", "definition": definition},
    ).json()
    stored = json.loads(workflow["definition_json"])
    run = client.post(
        f"/api/workflows/{workflow['id']}/run",
        json={"input": {"task": "验证可视化工作流"}},
    ).json()

    assert stored["nodes"][1]["position"] == {"x": 280, "y": 180}
    assert stored["edges"] == definition["edges"]
    assert run["status"] == "completed"


def test_workflow_knowledge_node_searches_bound_knowledge_base(client):
    knowledge_base = client.post(
        "/api/knowledge-bases",
        json={
            "name": "Workflow Knowledge Source",
            "discipline": "testing",
            "description": "Used to verify the workflow knowledge node.",
        },
    ).json()
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "Input"},
            {
                "id": "knowledge",
                "type": "knowledge",
                "label": "Knowledge search",
                "config": {
                    "knowledge_base_id": knowledge_base["id"],
                    "query": "{{input.task}}",
                    "top_k": 3,
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "Output",
                "config": {
                    "value": {
                        "answer": "{{nodes.knowledge.output}}",
                        "chunk_count": "{{nodes.knowledge.chunk_count}}",
                    }
                },
            },
        ],
        "edges": [
            {"source": "input", "target": "knowledge"},
            {"source": "knowledge", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "Knowledge node workflow",
            "description": "Verifies knowledge nodes can run independently.",
            "definition": definition,
        },
    ).json()

    run = client.post(
        f"/api/workflows/{workflow['id']}/run",
        json={"input": {"task": "Find the relevant workflow context."}},
    ).json()
    output = json.loads(run["output_json"])
    trace = json.loads(run["trace_json"])

    assert run["status"] == "completed"
    assert output["chunk_count"] == 0
    assert knowledge_base["name"] in output["answer"]
    assert [step["type"] for step in trace] == ["input", "knowledge", "output"]


def test_workflow_stream_reports_live_node_progress(client):
    planner = next(item for item in client.get("/api/agents").json() if item["slug"] == "planner")
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入"},
            {
                "id": "agent_stream",
                "type": "agent",
                "label": "流式 Agent",
                "config": {"agent_id": planner["id"], "input": "{{input.task}}"},
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": {"result": "{{nodes.agent_stream.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "agent_stream"},
            {"source": "agent_stream", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "流式工作流回归", "description": "节点进度", "definition": definition},
    ).json()

    response = client.post(
        f"/api/workflows/{workflow['id']}/run/stream",
        json={"input": {"task": "验证流式工作流"}},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    step_types = [event["step"]["type"] for event in events if event["type"] == "step"]
    agent_steps = [
        event["step"]
        for event in events
        if event["type"] == "step"
        and event["step"]["type"] == "workflow_agent_event"
    ]
    completed_steps = [
        event["step"]
        for event in events
        if event["type"] == "step"
        and event["step"]["type"] == "workflow_node_completed"
    ]
    result = next(event["run"] for event in events if event["type"] == "workflow_result")

    assert response.status_code == 200
    assert "stream_connected" in step_types
    assert step_types.count("workflow_node_started") == 3
    assert step_types.count("workflow_node_completed") == 3
    assert agent_steps
    assert {"run_started", "model_response", "run_completed"}.issubset(
        {step["agent_event"]["type"] for step in agent_steps}
    )
    assert next(
        step for step in completed_steps if step["node_id"] == "agent_stream"
    )["result"]["output_preview"]
    assert result["status"] == "completed"
    assert events[-1]["type"] == "done"


def test_workflow_stream_marks_failed_node_with_readable_error(client):
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入"},
            {
                "id": "missing_agent",
                "type": "agent",
                "label": "不可用 Agent",
                "config": {
                    "agent_id": "00000000-0000-0000-0000-000000000000",
                    "input": "{{input.task}}",
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": "{{nodes.missing_agent.output}}"},
            },
        ],
        "edges": [
            {"source": "input", "target": "missing_agent"},
            {"source": "missing_agent", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "失败节点可视化", "description": "节点失败事件", "definition": definition},
    ).json()

    response = client.post(
        f"/api/workflows/{workflow['id']}/run/stream",
        json={"input": {"task": "验证失败节点"}},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    failed_step = next(
        event["step"]
        for event in events
        if event["type"] == "step"
        and event["step"]["type"] == "workflow_node_failed"
    )
    result = next(event["run"] for event in events if event["type"] == "workflow_result")

    assert failed_step["node_id"] == "missing_agent"
    assert failed_step["label"] == "不可用 Agent"
    assert "Agent 不存在或未启用" in failed_step["error"]
    assert result["status"] == "failed"


def test_workflow_run_security_waits_for_inline_approval_and_exposes_reconnect_state(
    client, tmp_path
):
    workspace = tmp_path / "workflow-approval"
    workspace.mkdir()
    client.put(
        "/api/security/runtime",
        json={
            "filesystem_mode": "custom",
            "workspace_roots": [str(workspace)],
            "command_mode": "risk_based",
            "block_critical_commands": True,
        },
    )
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入"},
            {
                "id": "write",
                "type": "tool",
                "label": "写入文件",
                "config": {
                    "tool": "write_file",
                    "arguments": {"path": "approved.txt", "content": "workflow-approved"},
                    "permission_mode": "auto",
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": "{{nodes.write.output}}"},
            },
        ],
        "edges": [
            {"source": "input", "target": "write"},
            {"source": "write", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "工作流审批恢复测试", "description": "审批与状态恢复", "definition": definition},
    ).json()
    existing_ids = {item["id"] for item in client.get("/api/approvals").json()}

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                client.post,
                f"/api/workflows/{workflow['id']}/run",
                json={
                    "input": {"task": "写入测试文件"},
                    "security_profile": "custom",
                    "permission_mode": "ask",
                },
            )
            running = None
            approval = None
            for _attempt in range(120):
                rows = client.get(
                    f"/api/workflow-runs?workflow_id={workflow['id']}&status=running"
                ).json()
                running = rows[0] if rows else None
                pending = [
                    item
                    for item in client.get("/api/approvals?status=pending").json()
                    if item["id"] not in existing_ids
                ]
                approval = pending[0] if pending else None
                if running and approval:
                    break
                time.sleep(0.05)

            assert running is not None
            assert approval is not None
            assert approval["run_id"] == running["id"]
            scoped = client.get(
                f"/api/approvals?status=pending&run_id={running['id']}"
            ).json()
            assert [item["id"] for item in scoped] == [approval["id"]]
            buffered = client.get(
                f"/api/workflow-runs/{running['id']}/events?after=0"
            ).json()
            assert buffered["active"] is True
            assert any(
                event.get("agent_event", {}).get("type") == "approval_required"
                for event in buffered["events"]
            )

            decision = client.post(
                f"/api/approvals/{approval['id']}/decide",
                json={"approved": True, "decided_by": "pytest-workflow"},
            )
            assert decision.status_code == 200
            response = future.result(timeout=10)

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert (workspace / "approved.txt").read_text("utf-8") == "workflow-approved"
    finally:
        client.put(
            "/api/security/runtime",
            json={
                "filesystem_mode": "workspace",
                "workspace_roots": ["data/workspace"],
                "command_mode": "risk_based",
                "block_critical_commands": True,
            },
        )


def test_professional_workflow_branches_loops_and_persists_artifacts(client):
    definition = {
        "variables": [
            {
                "name": "route",
                "type": "string",
                "default": "通过",
                "description": "条件分支测试变量",
            }
        ],
        "execution": {
            "loop_enabled": True,
            "loop_count": 2,
            "artifact_enabled": True,
        },
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入"},
            {
                "id": "assign",
                "type": "variable",
                "label": "设置路由",
                "config": {
                    "assignments": [
                        {
                            "name": "route",
                            "operation": "set",
                            "value": "{{input.route}}",
                        }
                    ]
                },
            },
            {
                "id": "gate",
                "type": "condition",
                "label": "质量分支",
                "config": {
                    "left": "{{variables.route}}",
                    "operator": "equals",
                    "right": "通过",
                },
            },
            {
                "id": "accepted",
                "type": "template",
                "label": "通过模板",
                "config": {"template": "已通过：{{input.task}}"},
            },
            {
                "id": "rejected",
                "type": "template",
                "label": "驳回模板",
                "config": {"template": "待修改：{{input.task}}"},
            },
            {
                "id": "merge",
                "type": "merge",
                "label": "分支聚合",
                "config": {"mode": "text", "separator": "\n"},
            },
            {
                "id": "artifact",
                "type": "artifact",
                "label": "专业交付",
                "config": {
                    "title": "分支结果",
                    "content": "# 结果\n\n{{nodes.merge.output}}",
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": {"result": "{{nodes.artifact.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "assign", "source_slot": "output"},
            {"source": "assign", "target": "gate", "source_slot": "output"},
            {"source": "gate", "target": "accepted", "source_slot": "true"},
            {"source": "gate", "target": "rejected", "source_slot": "false"},
            {"source": "accepted", "target": "merge", "source_slot": "output"},
            {"source": "rejected", "target": "merge", "source_slot": "output"},
            {"source": "merge", "target": "artifact", "source_slot": "output"},
            {"source": "artifact", "target": "output", "source_slot": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={
            "name": "专业分支循环工作流",
            "description": "验证变量、槽位、条件分支、循环与产出文档",
            "definition": definition,
        },
    ).json()
    run = client.post(
        f"/api/workflows/{workflow['id']}/run",
        json={"input": {"task": "形成最终方案", "route": "通过"}},
    ).json()
    trace = json.loads(run["trace_json"])
    output = json.loads(run["output_json"])
    artifacts = client.get(f"/api/workflow-runs/{run['id']}/artifacts").json()

    assert run["status"] == "completed"
    assert run["iteration_count"] == 2
    assert "已通过：形成最终方案" in output["result"]
    assert sum(
        step["status"] == "skipped" and step["node_id"] == "rejected"
        for step in trace
    ) == 2
    assert len(artifacts) == 4
    assert {item["iteration"] for item in artifacts} == {1, 2}
    assert all(item["run_id"] == run["id"] for item in artifacts)
    assert all('{"result"' not in item["content"] for item in artifacts)

    artifact_export = client.post(
        f"/api/workflow-artifacts/{artifacts[-1]['id']}/export/docx", json={}
    )
    assert artifact_export.status_code == 200
    assert artifact_export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "filename*=UTF-8''" in artifact_export.headers["content-disposition"]
    artifact_document = Document(BytesIO(artifact_export.content))
    assert any("结果" in paragraph.text for paragraph in artifact_document.paragraphs)

    run_export = client.post(f"/api/workflow-runs/{run['id']}/export/docx", json={})
    assert run_export.status_code == 200
    assert run_export.content.startswith(b"PK")


def test_workflow_expert_builds_editable_executable_draft(client):
    response = client.post(
        "/api/workflow-expert/chat",
        json={
            "message": "请编排一个需要知识库检索、Agent 分析并循环 3 次的工作流，每轮生成产出文档。",
            "history": [],
            "workflow_name": "",
            "workflow_description": "",
        },
    )
    result = response.json()
    definition = result["definition"]

    assert response.status_code == 200
    assert definition["execution"]["loop_enabled"] is True
    assert definition["execution"]["loop_count"] == 3
    assert any(node["type"] == "agent" for node in definition["nodes"])
    assert any(node["type"] == "artifact" for node in definition["nodes"])
    assert any(node["type"] == "output" for node in definition["nodes"])
    assert definition["variables"]
    assert result["resource_snapshot"]["agent_count"] >= 1


def test_workflow_expert_creates_new_agents_and_executable_branches(client):
    for endpoint in client.get("/api/model-endpoints").json():
        client.patch(
            f"/api/model-endpoints/{endpoint['id']}",
            json={"enabled": False},
        )
    response = client.post(
        "/api/workflow-expert/chat",
        json={
            "message": (
                "请创建一个新的需求分析 Agent 和一个质量审核 Agent，"
                "建立通过与不通过的新分支，循环 2 次，每轮生成产出文档。"
            ),
            "history": [],
            "workflow_name": "智能需求评审流",
            "workflow_description": "验证新 Agent 与条件支路的完整实体化",
        },
    )
    assert response.status_code == 200
    proposal = response.json()
    definition = proposal["definition"]
    draft_keys = {item["key"] for item in proposal["agent_drafts"]}

    assert len(proposal["agent_drafts"]) >= 2
    assert {"需求分析 Agent", "质量审核 Agent"}.issubset(
        {item["name"] for item in proposal["agent_drafts"]}
    )
    assert all(item["system_prompt"] for item in proposal["agent_drafts"])
    assert all("exec" in item["tools"] for item in proposal["agent_drafts"])
    assert all(item["rag_config"] for item in proposal["agent_drafts"])
    assert all(item["generation_config"] for item in proposal["agent_drafts"])
    assert {
        node["config"]["agent_draft_key"]
        for node in definition["nodes"]
        if node["type"] == "agent" and node["config"].get("agent_draft_key")
    } == draft_keys
    assert any(node["type"] == "condition" for node in definition["nodes"])
    assert {"true", "false"}.issubset(
        {
            edge["source_slot"]
            for edge in definition["edges"]
            if edge["source_slot"] in {"true", "false"}
        }
    )
    assert definition["execution"]["loop_count"] == 2

    materialized = client.post(
        "/api/workflow-expert/materialize",
        json={"proposal": proposal},
    )
    assert materialized.status_code == 200
    result = materialized.json()
    created = result["created_agents"]
    enabled_skill_ids = {
        item["id"] for item in client.get("/api/skills").json() if item["enabled"]
    }
    enabled_mcp_ids = {
        item["id"]
        for item in client.get("/api/extensions").json()
        if item["enabled"] and item["kind"] == "mcp"
    }
    assert len(created) == len(draft_keys)
    assert all(item["status"] == "candidate" for item in created)
    assert all(
        {"exec", "call_agent", "web_research", "read_file", "write_file"}.issubset(
            set(json.loads(item["tools_json"]))
        )
        for item in created
    )
    assert all(
        enabled_skill_ids.issubset(set(json.loads(item["skills_json"])))
        for item in created
    )
    assert all(
        enabled_mcp_ids.issubset(
            set(json.loads(item["permissions_json"])["mcp_extensions"])
        )
        for item in created
    )
    assert all(
        json.loads(item["permissions_json"])["mcp"]
        and json.loads(item["permissions_json"])["skills"]
        for item in created
    )
    assert all(
        json.loads(item["generation_config_json"])["max_output_tokens"] >= 8192
        for item in created
    )
    assert all(json.loads(item["generation_config_json"]) for item in created)
    assert not result["agent_drafts"]
    assert all(
        node["config"].get("agent_id")
        and "agent_draft_key" not in node["config"]
        for node in result["definition"]["nodes"]
        if node["type"] == "agent"
    )

    workflow = client.post(
        "/api/workflows",
        json={
            "name": "新 Agent 分支执行验收",
            "description": "验证专家生成结果可以直接执行",
            "definition": result["definition"],
        },
    )
    assert workflow.status_code == 201
    run = client.post(
        f"/api/workflows/{workflow.json()['id']}/run",
        json={"input": {"task": "分析需求并完成质量审核"}},
    )
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["iteration_count"] == 2
    assert client.get(
        f"/api/workflow-runs/{run.json()['id']}/artifacts"
    ).json()


def test_offline_agent_create_and_existing_agent_update(client):
    created = client.post(
        "/api/agents",
        json={
            "name": "测试编辑 Agent",
            "slug": "editable-agent",
            "description": "验证离线创建和编辑设置",
            "system_prompt": "你是一个用于验证 Agent 创建和设置修改的测试智能体。",
            "provider": "demo",
            "model_endpoint_id": "",
            "model": "demo-model",
            "temperature": 0.2,
            "tools": ["read_file"],
            "skills": [],
            "knowledge_bases": [],
            "permissions": {"tool_mode": "ask"},
        },
    )
    assert created.status_code == 201
    assert created.json()["model_endpoint_id"] is None

    updated = client.patch(
        f"/api/agents/{created.json()['id']}",
        json={
            "name": "测试编辑后的 Agent",
            "temperature": 0.6,
            "model_endpoint_id": None,
            "tools": ["read_file", "search_files"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "测试编辑后的 Agent"
    assert updated.json()["temperature"] == 0.6
    assert json.loads(updated.json()["tools_json"]) == ["read_file", "search_files", "exec"]


def test_agent_rag_settings_preview_and_complete_five_point_context(client):
    suffix = str(time.time_ns())
    knowledge_base = client.post(
        "/api/knowledge-bases",
        json={
            "name": f"虚拟内存专项-{suffix}",
            "discipline": "计算机",
            "description": "验证每 Agent RAG 全链路",
        },
    ).json()
    document = client.post(
        f"/api/knowledge-bases/{knowledge_base['id']}/documents/text",
        json={
            "title": "虚拟内存五点说明",
            "source": "操作系统课程",
            "content": (
                "虚拟内存具有以下五点核心内容：\n"
                "1. 通过地址映射扩展进程可见的内存空间。\n"
                "2. 使用页表将虚拟页关联到物理页框。\n"
                "3. 借助页面置换缓解物理内存容量不足。\n"
                "4. 通过进程地址空间隔离提高系统安全性。\n"
                "5. 按需加载页面以降低程序启动时的内存占用。"
            ),
        },
    )
    assert document.status_code == 201
    created = client.post(
        "/api/agents",
        json={
            "name": "虚拟内存 RAG Agent",
            "slug": f"virtual-memory-rag-{suffix}",
            "description": "验证加权混合检索、多轮改写和完整列表扩展",
            "system_prompt": "你是操作系统知识助手，必须根据检索证据完整回答问题。",
            "knowledge_bases": [knowledge_base["id"]],
            "rag_config": {
                "enabled": True,
                "similarity_threshold": 0,
                "dense_weight": 0.7,
                "lexical_weight": 0.3,
                "candidate_k": 24,
                "rerank_k": 10,
                "top_k": 6,
                "context_char_budget": 8000,
                "query_rewrite": True,
                "multi_turn": True,
                "cross_language": False,
                "knowledge_graph": True,
                "parent_expansion": True,
                "complete_list_expansion": True,
            },
            "generation_config": {
                "opening_message": "我会完整检索并引用操作系统知识。",
                "suggested_questions": ["虚拟内存包括哪五点？"],
                "prompt_template": "证据：\n{knowledge}\n历史：{history}\n引用：{citations}\n问题：{question}",
                "citation_required": True,
                "verify_answer": True,
            },
        },
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    saved_rag = json.loads(agent["rag_config_json"])
    saved_generation = json.loads(agent["generation_config_json"])
    assert saved_rag["dense_weight"] == 0.7
    assert saved_rag["knowledge_graph"] is True
    assert saved_generation["opening_message"].startswith("我会完整检索")

    preview = client.post(
        f"/api/agents/{agent['id']}/rag/preview",
        json={
            "query": "它包括哪五点？请完整列出。",
            "history": [{"role": "user", "content": "虚拟内存是什么？"}],
        },
    )
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["standalone_query"].startswith("虚拟内存是什么")
    assert "1. 通过地址映射" in result["context"]
    assert "5. 按需加载页面" in result["context"]
    assert result["trace"]["dense_weight"] == 0.7
    assert result["trace"]["lexical_weight"] == 0.3
    assert result["trace"]["knowledge_graph"] is True
    assert result["trace"]["exhaustive_query"] is True
    assert "{knowledge}" not in result["rendered_prompt"]
    assert any(item["type"] == "context_assembled" for item in result["pipeline"])
    run = client.post(
        f"/api/agents/{agent['id']}/run",
        json={"input": "虚拟内存包括哪五点？请完整列出。"},
    )
    assert run.status_code == 200
    run_data = run.json()
    assert run_data["status"] == "completed"
    assert "5. 按需加载页面" in run_data["output_text"]
    verification = next(
        item
        for item in json.loads(run_data["trace_json"])
        if item["type"] == "generation_verified"
    )
    assert verification["passed"] is True
    evaluation_case = client.post(
        "/api/evaluation-cases",
        json={
            "name": f"虚拟内存五点完整性-{suffix}",
            "discipline": "计算机",
            "category": "evidence",
            "input": "虚拟内存包括哪五点？",
            "expected_keywords": ["地址映射", "页表", "页面置换", "地址空间隔离", "按需加载"],
            "requires_citation": True,
        },
    )
    assert evaluation_case.status_code == 201
    evaluation = client.post(
        f"/api/agents/{agent['id']}/rag/evaluate",
        json={"case_ids": [evaluation_case.json()["id"]]},
    )
    assert evaluation.status_code == 200
    evaluation_data = evaluation.json()
    assert evaluation_data["summary"]["cases"] == 1
    assert evaluation_data["summary"]["recall_at_k"] == 1
    assert evaluation_data["summary"]["mrr"] == 1
    assert evaluation_data["results"][0]["list_items"] == 5
    assert (
        client.delete(
            f"/api/evaluation-cases/{evaluation_case.json()['id']}"
        ).status_code
        == 204
    )


def test_agent_rag_rejects_zero_hybrid_weights(client):
    response = client.post(
        "/api/agents",
        json={
            "name": "非法权重 Agent",
            "slug": f"invalid-rag-{time.time_ns()}",
            "system_prompt": "你是用于验证 RAG 参数校验的测试智能体。",
            "rag_config": {"dense_weight": 0, "lexical_weight": 0},
        },
    )
    assert response.status_code == 422
    assert "不能同时为 0" in response.text


def test_custom_agent_group_crud_and_membership(client):
    suffix = str(time.time_ns())
    created_group = client.post(
        "/api/agent-groups",
        json={
            "name": f"自定义科研组-{suffix}",
            "description": "集中管理科研辅助 Agent",
            "color": "#7c5cc4",
            "sort_order": 80,
        },
    )
    assert created_group.status_code == 201, created_group.text
    group = created_group.json()
    assert group["agent_count"] == 0

    created_agent = client.post(
        "/api/agents",
        json={
            "name": "分组测试 Agent",
            "slug": f"group-agent-{suffix}",
            "description": "验证 Agent 自定义分组",
            "system_prompt": "你是用于验证自定义分组功能的测试 Agent。",
            "group_id": group["id"],
        },
    )
    assert created_agent.status_code == 201, created_agent.text
    agent = created_agent.json()
    assert agent["group_id"] == group["id"]

    groups = client.get("/api/agent-groups")
    assert groups.status_code == 200
    assert next(item for item in groups.json() if item["id"] == group["id"])["agent_count"] == 1

    renamed = client.patch(
        f"/api/agent-groups/{group['id']}",
        json={"name": f"科研协作组-{suffix}", "color": "#2f80d4"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == f"科研协作组-{suffix}"

    deleted = client.delete(f"/api/agent-groups/{group['id']}")
    assert deleted.status_code == 204
    refreshed_agent = client.get(f"/api/agents/{agent['id']}")
    assert refreshed_agent.status_code == 200
    assert refreshed_agent.json()["group_id"] is None


def test_workflow_planning_policy_uses_one_model_call_and_no_tools(client, monkeypatch):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse

    calls: list[list[dict]] = []

    class PlanningBudgetProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            calls.append(tools or [])
            if tools:
                return LLMResponse(
                    content="",
                    tokens=3,
                    tool_calls=[
                        {
                            "id": "should-not-run",
                            "name": "list_directory",
                            "arguments": {"path": "."},
                        }
                    ],
                )
            return LLMResponse(content="# 综述提纲\n\n1. 研究范围\n2. 证据要求", tokens=5)

    provider = PlanningBudgetProvider()
    monkeypatch.setattr(agents_service, "get_provider", lambda _provider: provider)
    agent = client.post(
        "/api/agents",
        json={
            "name": "工具预算规划 Agent",
            "slug": "tool-budget-planning-agent",
            "system_prompt": "根据用户目标输出结构完整、可执行、可验证的研究提纲。",
            "provider": "planning-budget-test",
            "model": "test-model",
            "tools": ["list_directory", "read_file", "exec"],
            "permissions": {"tool_mode": "auto"},
        },
    ).json()
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入", "config": {}},
            {
                "id": "plan",
                "type": "agent",
                "label": "综述提纲规划",
                "config": {
                    "agent_id": agent["id"],
                    "auto_input": True,
                    "tool_policy": "auto",
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": {"result": "{{nodes.plan.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "plan"},
            {"source": "plan", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "规划节点预算回归", "description": "禁止无关工具调用", "definition": definition},
    ).json()
    response = client.post(
        f"/api/workflows/{workflow['id']}/run/stream",
        json={"input": {"task": "为网格质量评估综述规划提纲"}},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    agent_events = [
        item["step"]["agent_event"]
        for item in events
        if item.get("type") == "step"
        and item.get("step", {}).get("type") == "workflow_agent_event"
    ]

    assert len(calls) == 1
    assert calls == [[]]
    policy = next(item for item in agent_events if item["type"] == "tool_policy_applied")
    completed = next(item for item in agent_events if item["type"] == "run_completed")
    assert policy["preset"] == "planning"
    assert policy["max_calls"] == 0
    assert completed["model_calls"] == 1
    assert completed["tool_calls_executed"] == 0


def test_workflow_research_policy_collects_once_then_calls_model_once(client, monkeypatch):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse
    from backend.app.services.web_research import web_research_service

    model_calls = 0
    collected_tasks: list[str] = []

    class ResearchBudgetProvider:
        async def chat(self, messages, *, model, temperature, tools=None, **_kwargs):
            nonlocal model_calls
            model_calls += 1
            assert tools is None
            assert "https://example.org/paper" in messages[0]["content"]
            return LLMResponse(content="已基于实时来源形成证据表。", tokens=7)

    async def fake_collect(task, on_event):
        collected_tasks.append(task)
        await on_event({"type": "research_planning", "queries": ["mesh quality"]})
        return [
            {
                "title": "Verified paper",
                "url": "https://example.org/paper",
                "source": "Crossref",
                "content": "Verified evidence.",
            }
        ]

    monkeypatch.setattr(
        agents_service,
        "get_provider",
        lambda _provider: ResearchBudgetProvider(),
    )
    monkeypatch.setattr(web_research_service, "collect", fake_collect)
    agent = client.post(
        "/api/agents",
        json={
            "name": "单次联网检索 Agent",
            "slug": "single-pass-research-agent",
            "system_prompt": "先检索真实资料，再形成结构化证据表。",
            "provider": "research-budget-test",
            "model": "test-model",
            "tools": ["web_research", "exec"],
        },
    ).json()
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入", "config": {}},
            {
                "id": "research",
                "type": "agent",
                "label": "前沿文献检索",
                "config": {
                    "agent_id": agent["id"],
                    "auto_input": True,
                    "tool_policy": "auto",
                    "retry_count": 0,
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": {"result": "{{nodes.research.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "research"},
            {"source": "research", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "单次联网检索回归", "description": "成本受控", "definition": definition},
    ).json()
    response = client.post(
        f"/api/workflows/{workflow['id']}/run/stream",
        json={"input": {"task": "请执行前沿文献检索并分析近十年论文"}},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    agent_events = [
        item["step"]["agent_event"]
        for item in events
        if item.get("type") == "step"
        and item.get("step", {}).get("type") == "workflow_agent_event"
    ]

    policy = next(item for item in agent_events if item["type"] == "tool_policy_applied")
    assert len(collected_tasks) == 1
    assert model_calls == 1
    assert policy["deterministic_research"] is True
    assert policy["available_tools"] == ["web_research"]
    assert any(item["type"] == "research_context_ready" for item in agent_events)


def test_workflow_reuses_duplicate_tool_call_then_converges(client, monkeypatch):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse

    model_calls = 0

    class DuplicateToolProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            nonlocal model_calls
            model_calls += 1
            if not tools:
                return LLMResponse(content="已根据一次真实目录结果完成检查。", tokens=3)
            return LLMResponse(
                content="",
                tokens=2,
                tool_calls=[
                    {
                        "id": f"duplicate-{model_calls}",
                        "name": "list_directory",
                        "arguments": {"path": "."},
                    }
                ],
            )

    monkeypatch.setattr(
        agents_service, "get_provider", lambda _provider: DuplicateToolProvider()
    )
    agent = client.post(
        "/api/agents",
        json={
            "name": "重复调用去重 Agent",
            "slug": "duplicate-tool-dedupe-agent",
            "system_prompt": "检查一次真实结果后立即形成结论，不重复执行相同参数。",
            "provider": "duplicate-tool-test",
            "model": "test-model",
            "tools": ["list_directory"],
            "permissions": {"tool_mode": "auto"},
        },
    ).json()
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入", "config": {}},
            {
                "id": "inspect",
                "type": "agent",
                "label": "执行项目检查",
                "config": {
                    "agent_id": agent["id"],
                    "auto_input": True,
                    "tool_policy": "balanced",
                    "max_tool_iterations": 3,
                    "max_tool_calls": 6,
                },
            },
            {
                "id": "output",
                "type": "output",
                "label": "结果输出",
                "config": {"value": {"result": "{{nodes.inspect.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "inspect"},
            {"source": "inspect", "target": "output"},
        ],
    }
    workflow = client.post(
        "/api/workflows",
        json={"name": "重复工具调用去重回归", "description": "缓存相同结果", "definition": definition},
    ).json()
    response = client.post(
        f"/api/workflows/{workflow['id']}/run/stream",
        json={"input": {"task": "完成项目状态检查"}, "permission_mode": "auto"},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    agent_events = [
        item["step"]["agent_event"]
        for item in events
        if item.get("type") == "step"
        and item.get("step", {}).get("type") == "workflow_agent_event"
    ]
    completed = next(item for item in agent_events if item["type"] == "run_completed")

    assert model_calls == 3
    assert sum(item["type"] == "tool_result" for item in agent_events) == 1
    assert sum(item["type"] == "tool_result_reused" for item in agent_events) == 1
    assert completed["tool_calls_requested"] == 2
    assert completed["tool_calls_executed"] == 1
    assert completed["tool_calls_reused"] == 1


def test_missing_file_tool_result_does_not_abort_agent(client, monkeypatch):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse

    class MissingFileThenRecoverProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            if any(message.get("role") == "tool" for message in messages):
                return LLMResponse(content="文件缺失已记录，已改用现有上下文继续完成任务。", tokens=8)
            return LLMResponse(
                content="",
                tokens=4,
                tool_calls=[
                    {
                        "id": "missing-file-call",
                        "name": "read_file",
                        "arguments": {"path": "artifacts/does-not-exist.md"},
                    }
                ],
            )

    monkeypatch.setattr(
        agents_service,
        "get_provider",
        lambda _provider: MissingFileThenRecoverProvider(),
    )
    created = client.post(
        "/api/agents",
        json={
            "name": "缺失文件恢复 Agent",
            "slug": "missing-file-recovery-agent",
            "description": "验证工具失败恢复",
            "system_prompt": "遇到缺失文件时记录问题，并继续使用已有上下文完成任务。",
            "provider": "missing-file-test",
            "model": "test-model",
            "tools": ["read_file", "list_directory"],
            "permissions": {"tool_mode": "auto"},
        },
    ).json()
    run = client.post(
        f"/api/agents/{created['id']}/run",
        json={"input": "请读取旧成果后完成规划"},
    ).json()

    assert run["status"] == "completed"
    assert "继续完成任务" in run["output_text"]
    trace = json.loads(run["trace_json"])
    failed_tool = next(item for item in trace if item.get("type") == "tool_result")
    assert failed_tool["status"] == "failed"
    assert "文件不存在" in failed_tool["error"]


def test_builtin_plugins_skills_and_mcp_are_ready(client):
    skills = client.get("/api/skills").json()
    extensions = client.get("/api/extensions").json()
    assert {"研究问题与实验设计", "引用与事实核验", "数据隐私与科研伦理", "结构化成果交付"} <= {
        item["name"] for item in skills
    }
    assert {"Office 学术文档解析器", "Citation Guard 引用守卫", "Research Exporter 成果导出器"} <= {
        item["name"] for item in extensions
    }
    assert {"本地工作区 MCP", "学科知识库 MCP"} <= {item["name"] for item in extensions}

    initialized = client.post(
        "/api/mcp/workspace",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    ).json()
    listed = client.post(
        "/api/mcp/workspace",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()
    knowledge = client.post(
        "/api/mcp/knowledge",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "knowledge_search", "arguments": {"query": "科研伦理"}},
        },
    ).json()
    assert initialized["result"]["serverInfo"]["name"] == "EvoAgent Workspace MCP"
    assert {item["name"] for item in listed["result"]["tools"]} == {
        "list_directory",
        "read_file",
        "search_files",
    }
    assert knowledge["result"]["structuredContent"]


def test_agent_has_exec_skills_and_can_call_selected_mcp(client, monkeypatch):
    from backend.app.services import agents as agents_service
    from backend.app.services.llm import LLMResponse

    captured: dict[str, object] = {}

    class CapabilityProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            captured["system"] = messages[0]["content"]
            captured["tools"] = tools or []
            if any(message.get("role") == "tool" for message in messages):
                return LLMResponse(content="已通过 MCP 取得知识库列表。", tokens=2)
            mcp_tool = next(
                item["function"]["name"]
                for item in tools or []
                if "knowledge_bases_list" in item["function"]["description"]
            )
            return LLMResponse(
                content="",
                tokens=2,
                tool_calls=[{"id": "mcp-list", "name": mcp_tool, "arguments": {}}],
            )

    monkeypatch.setattr(agents_service, "get_provider", lambda _provider: CapabilityProvider())
    knowledge_mcp = next(
        item
        for item in client.get("/api/extensions").json()
        if item["name"] == "学科知识库 MCP"
    )
    created = client.post(
        "/api/agents",
        json={
            "name": "全能力 Agent",
            "slug": "full-capability-agent",
            "system_prompt": "理解用户目标，使用可用能力完成任务并报告真实执行结果。",
            "provider": "capability-test",
            "tools": [],
            "skills": [],
            "permissions": {
                "tool_mode": "auto",
                "mcp_extensions": [knowledge_mcp["id"]],
                "security_profile": "workspace_auto",
            },
        },
    ).json()
    assert "exec" in json.loads(created["tools_json"])

    run = client.post(
        f"/api/agents/{created['id']}/run", json={"input": "列出当前知识库"}
    ).json()
    trace = json.loads(run["trace_json"])
    tool_names = {item["function"]["name"] for item in captured["tools"]}
    context = next(item for item in trace if item["type"] == "context_ready")

    assert run["status"] == "completed"
    assert "exec" in tool_names
    assert any(name.startswith("mcp_") for name in tool_names)
    assert "【已启用 Skills】" in captured["system"]
    assert context["capabilities"]["exec"] is True
    assert context["capabilities"]["mcp_services"] == ["学科知识库 MCP"]
    assert any(item["type"] == "intent_detected" for item in trace)
    assert any(item["type"] == "tool_result" and item["status"] == "completed" for item in trace)


def test_evolution_requires_evaluation_before_activation(client, monkeypatch):
    from backend.app.services import evolution as evolution_service_module

    async def fake_evolution_research(_task, on_event):
        await on_event(
            {
                "type": "research_planning",
                "queries": ["agent prompt optimization reliability"],
                "mode": "web",
            }
        )
        return [
            {
                "title": "Reliable Agent Design Guide",
                "url": "https://example.edu/reliable-agent-design",
                "source": "Example University",
                "content": "Define measurable goals, use staged execution, recover from tool failures, and verify outputs.",
                "credibility": {"level": "高", "score": 90, "reason": "高校来源"},
            }
        ]

    monkeypatch.setattr(
        evolution_service_module.web_research_service,
        "collect",
        fake_evolution_research,
    )
    agent = next(item for item in client.get("/api/agents").json() if item["slug"] == "reviewer")
    proposal = client.post(
        "/api/evolution",
        json={
            "agent_id": agent["id"],
            "reason": "强化引用检查",
            "proposed_prompt": "你是引用核验专家。逐条检查来源、证据、风险和待核验信息，并输出结构化报告。",
            "proposed_tools": [],
        },
    ).json()
    premature = client.post(
        f"/api/evolution/{proposal['id']}/decide",
        json={"approved": True, "decided_by": "pytest"},
    )
    assert premature.status_code == 409
    response = client.post(f"/api/evolution/{proposal['id']}/evaluate/stream", json={})
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    step_types = [event["step"]["type"] for event in events if event["type"] == "step"]
    evaluated = next(
        event["proposal"] for event in events if event["type"] == "evolution_result"
    )
    assert "stream_connected" in step_types
    assert step_types.count("evaluation_case_started") == 3
    assert step_types.count("evaluation_case_completed") == 3
    assert "evolution_methods_ready" in step_types
    assert "evolution_prompt_optimized" in step_types
    assert "evolution_skill_packaged" in step_types
    assert "evolution_artifact_created" in step_types
    assert evaluated["status"] == "evaluated"
    report = json.loads(evaluated["report_json"])
    assert report["research_sources"][0]["url"].startswith("https://")
    assert report["skill"]["name"]
    assert report["skill"]["id"] in json.loads(
        next(
            item
            for item in client.get("/api/agents").json()
            if item["id"] == proposal["candidate_agent_id"]
        )["skills_json"]
    )
    assert "目标任务执行协议" in report["optimized_prompt"]
    assert report["artifact"]["relative_path"].endswith("-evaluation.md")
    assert report["gate"]["passed"] is True
    assert {item["id"] for item in report["gate"]["checks"]} == {
        "candidate_score",
        "improvement",
        "failure_rate",
    }
    assert report["cases"][0]["candidate_breakdown"]["coverage"] >= 0
    approved = client.post(
        f"/api/evolution/{proposal['id']}/decide",
        json={"approved": True, "decided_by": "pytest"},
    ).json()
    assert approved["status"] == "approved"
    lineages = client.get("/api/evolution/lineages").json()
    lineage = next(
        item
        for item in lineages
        if item["lineage_id"]
        == next(
            agent
            for agent in client.get("/api/agents").json()
            if agent["id"] == proposal["candidate_agent_id"]
        )["lineage_id"]
    )
    assert lineage["active_agent_id"] == proposal["candidate_agent_id"]
    rolled_back = client.post(
        f"/api/evolution/agents/{proposal['candidate_agent_id']}/rollback",
        json={
            "target_agent_id": proposal["source_agent_id"],
            "reason": "pytest 验证安全回滚",
            "actor": "pytest",
        },
    ).json()
    assert rolled_back["to_agent_id"] == proposal["source_agent_id"]


def test_evolution_goal_analysis_parallel_versions_and_case_management(client):
    agent = next(
        item for item in client.get("/api/agents").json() if item["status"] == "active"
    )
    analysis = client.post(
        "/api/evolution/analyze-goal",
        json={
            "agent_id": agent["id"],
            "goal": "提高长任务完整性，减少遗漏，并增强工具失败后的恢复能力。",
            "include_run_insights": True,
        },
    )
    assert analysis.status_code == 200
    diagnosis = analysis.json()
    dimension_ids = {item["id"] for item in diagnosis["dimensions"]}
    assert "structure" in dimension_ids
    assert "reliability" in dimension_ids
    assert "【本轮进化目标】" in diagnosis["recommended_prompt"]
    assert diagnosis["suggested_cases"]

    benchmark = client.post(
        "/api/evaluation-cases",
        json={
            "name": "并行草案临时用例",
            "discipline": "工程",
            "category": "reliability",
            "input": "工具失败后继续完成其余任务并说明替代方案。",
            "expected_keywords": ["失败", "替代", "完成"],
            "requires_citation": False,
            "weight": 1.7,
            "enabled": True,
        },
    ).json()
    updated = client.put(
        f"/api/evaluation-cases/{benchmark['id']}",
        json={"weight": 2.0, "enabled": False},
    ).json()
    assert updated["weight"] == 2.0
    assert updated["enabled"] is False

    payload = {
        "agent_id": agent["id"],
        "reason": diagnosis["goal"],
        "proposed_prompt": "",
        "selected_case_ids": [],
        "min_candidate_score": 72,
        "min_improvement": 1,
        "max_failure_rate": 0.1,
        "goal_analysis": diagnosis,
    }
    first = client.post("/api/evolution", json=payload)
    second = client.post("/api/evolution", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    first_candidate = next(
        item
        for item in client.get("/api/agents").json()
        if item["id"] == first.json()["candidate_agent_id"]
    )
    second_candidate = next(
        item
        for item in client.get("/api/agents").json()
        if item["id"] == second.json()["candidate_agent_id"]
    )
    assert second_candidate["version"] == first_candidate["version"] + 1
    assert second_candidate["slug"] != first_candidate["slug"]

    overview = client.get("/api/evolution/overview").json()
    assert overview["summary"]["total"] >= 2
    assert len(overview["pipeline"]) == 5
    assert client.delete(f"/api/evaluation-cases/{benchmark['id']}").status_code == 204


def test_user_auth_usage_memory_profile_and_reply_style(client, monkeypatch):
    from backend.app.services import agents as agents_module
    from backend.app.services.llm import LLMResponse

    captured_system_prompts: list[str] = []

    class CapturingProvider:
        async def chat(self, messages, *, model, temperature, tools=None):
            captured_system_prompts.append(messages[0]["content"])
            return LLMResponse(content="已完成用户画像测试。", tokens=37)

    monkeypatch.setattr(
        agents_module, "get_provider", lambda _provider: CapturingProvider()
    )
    monkeypatch.setattr(
        agents_module, "provider_from_endpoint", lambda _endpoint: CapturingProvider()
    )

    status = client.get("/api/auth/status").json()
    assert status["registration_required"] is True

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "local_tester",
            "display_name": "本地测试用户",
            "password": "secure-pass-2026",
        },
    )
    assert registered.status_code == 201
    auth = registered.json()
    assert auth["claimed_legacy_data"] is True
    headers = {"Authorization": f"Bearer {auth['token']}"}

    me = client.get("/api/auth/me", headers=headers).json()
    assert me["username"] == "local_tester"
    assert me["memory_enabled"] is True
    assert "password_hash" not in me

    custom = client.put(
        "/api/users/me/reply-style",
        headers=headers,
        json={
            "style_id": "custom",
            "custom_style": "先给结论，再用三条行动建议说明，并保持专业友好。",
        },
    )
    assert custom.status_code == 200
    assert custom.json()["reply_style_id"] == "custom"

    agent = next(
        item for item in client.get("/api/agents").json() if item["status"] == "active"
    )
    conversation = client.post(
        f"/api/agents/{agent['id']}/conversations",
        headers=headers,
        json={"title": "用户画像测试"},
    ).json()
    assert conversation["user_id"] == me["id"]

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        headers=headers,
        json={"content": "请帮我检查这份研究报告的证据质量与引用风险。"},
    )
    assert response.status_code == 200
    assert '"type": "done"' in response.text
    assert captured_system_prompts, response.text
    assert any(
        "先给结论，再用三条行动建议说明，并保持专业友好。" in prompt
        for prompt in captured_system_prompts
    )

    daily = client.get("/api/users/me/usage?range=day", headers=headers).json()
    weekly = client.get("/api/users/me/usage?range=week", headers=headers).json()
    monthly = client.get("/api/users/me/usage?range=month", headers=headers).json()
    assert len(daily["chart"]) == 7
    assert len(weekly["chart"]) == 8
    assert len(monthly["chart"]) == 12
    assert daily["summary"]["total_runs"] >= 1
    assert any(item["input"].startswith("请帮我检查") for item in daily["records"])

    profile = client.get("/api/users/me/profile", headers=headers).json()
    assert profile["question_count"] >= 1
    assert any("证据质量" in item["question"] for item in profile["recent_questions"])

    updated = client.patch(
        "/api/users/me",
        headers=headers,
        json={"display_name": "画像测试用户", "memory_enabled": False},
    ).json()
    assert updated["display_name"] == "画像测试用户"
    assert updated["memory_enabled"] is False

    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_production_agent_creation_forces_existing_online_endpoint(client):
    from backend.app.config import settings

    previous = settings.require_online_agents
    settings.require_online_agents = True
    try:
        endpoint = client.post(
            "/api/model-endpoints",
            json={
                "name": "生产在线路由测试",
                "modality": "chat",
                "provider_type": "openai-compatible",
                "base_url": "https://online-routing.example/v1",
                "api_key": "routing-secret",
                "default_model": "routing-model",
                "enabled": True,
            },
        )
        assert endpoint.status_code == 201
        endpoint_id = endpoint.json()["id"]

        created = client.post(
            "/api/agents",
            json={
                "name": "强制在线 Agent",
                "slug": "required-online-agent",
                "system_prompt": "始终使用已配置的在线接口完成真实模型推理任务。",
                "provider": "demo",
                "model": "demo-model",
            },
        )
        assert created.status_code == 201
        agent = created.json()
        assert agent["model_endpoint_id"] == endpoint_id
        assert agent["provider"] == "openai-compatible"
        assert agent["model"] == "routing-model"
    finally:
        settings.require_online_agents = previous
