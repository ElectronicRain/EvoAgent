from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx


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


def test_agent_conversation_streams_steps_and_keeps_history(client):
    planner = next(item for item in client.get("/api/agents").json() if item["slug"] == "planner")
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
    assert [step["type"] for step in first_steps] == [
        "stream_connected",
        "run_started",
        "intent_detected",
        "context_ready",
        "model_response",
        "run_completed",
        "knowledge_archived",
    ]
    assert first_events[-1]["type"] == "done"
    assert next(step for step in first_steps if step["type"] == "context_ready")[
        "history_messages"
    ] == 0
    assert any(event["type"] == "assistant" for event in first_events)
    archive_step = next(step for step in first_steps if step["type"] == "knowledge_archived")
    run_id = next(step["run_id"] for step in first_steps if step["type"] == "run_started")
    assert archive_step["run_id"] == run_id
    assert archive_step["knowledge_base_ids"]
    for knowledge_base_id in archive_step["knowledge_base_ids"]:
        documents = client.get(
            f"/api/knowledge-bases/{knowledge_base_id}/documents"
        ).json()
        archived_document = next(
            item for item in documents if item["source"] == f"Agent 运行 · {run_id}"
        )
        metadata = json.loads(archived_document["metadata_json"])
        assert archived_document["status"] == "ready"
        assert metadata["kind"] == "agent_task_result"
        assert metadata["conversation_id"] == conversation["id"]
        assert metadata["auto_archived"] is True

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


def test_agent_without_bound_knowledge_base_archives_to_default_base(client):
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
    archive_step = next(
        event["step"]
        for event in events
        if event["type"] == "step" and event["step"]["type"] == "knowledge_archived"
    )

    assert response.status_code == 200
    assert archive_step["knowledge_base_names"] == ["Agent 任务成果"]
    documents = client.get(
        f"/api/knowledge-bases/{archive_step['knowledge_base_ids'][0]}/documents"
    ).json()
    document = next(item for item in documents if item["id"] in archive_step["document_ids"])
    assert json.loads(document["metadata_json"])["agent_id"] == agent["id"]


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
        assert "knowledge_archived" in step_types
        assert [item["role"] for item in messages] == ["user", "assistant"]


def test_research_trace_survives_refresh_and_creates_markdown(
    client, monkeypatch, tmp_path
):
    from backend.app.config import settings
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

    monkeypatch.setattr(settings, "workspace_root", tmp_path)
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
    assert artifacts[0]["relative_path"].endswith(".md")
    assert (tmp_path / artifacts[0]["relative_path"]).is_file()
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
    result = next(event["run"] for event in events if event["type"] == "workflow_result")

    assert response.status_code == 200
    assert "stream_connected" in step_types
    assert step_types.count("workflow_node_started") == 3
    assert step_types.count("workflow_node_completed") == 3
    assert result["status"] == "completed"
    assert events[-1]["type"] == "done"


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
