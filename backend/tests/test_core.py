from __future__ import annotations

import asyncio
from datetime import date

import pytest

from backend.app.services.agents import AgentEngine, AgentToolPolicy
from backend.app.services.knowledge import chunk_text
from backend.app.services.intent import intent_service
from backend.app.services.secrets import secret_store
from backend.app.services.tools import ToolRuntime
from backend.app.services.workflows import (
    WorkflowControl,
    WorkflowEngine,
    _condition_expression,
    render_value,
)
from backend.app.services.web_research import WebResearchService


def test_template_rendering_preserves_typed_values():
    context = {"input": {"count": 3}, "nodes": {"a": {"output": "done"}}}
    assert render_value("{{input.count}}", context) == 3
    assert render_value("result={{nodes.a.output}}", context) == "result=done"


def test_workflow_cycle_detection():
    definition = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    }
    with pytest.raises(ValueError, match="循环"):
        WorkflowEngine()._order_nodes(definition)


def test_workflow_definition_requires_complete_condition_branches():
    definition = {
        "nodes": [
            {"id": "input", "type": "input"},
            {"id": "gate", "type": "condition", "label": "质量门"},
            {"id": "output", "type": "output"},
        ],
        "edges": [
            {"source": "input", "target": "gate"},
            {"source": "gate", "target": "output", "source_slot": "true"},
        ],
    }

    with pytest.raises(ValueError, match="TRUE 和 FALSE"):
        WorkflowEngine().validate_definition(definition)


def test_workflow_node_retry_only_accepts_transient_failures():
    engine = WorkflowEngine()

    assert engine._retryable_node_error(RuntimeError("ReadTimeout")) is True
    assert engine._retryable_node_error(RuntimeError("HTTP 503: busy")) is True
    assert engine._retryable_node_error(RuntimeError("HTTP 402: insufficient")) is False
    assert engine._retryable_node_error(RuntimeError("HTTP 503（已尝试 3 次）")) is False
    assert engine._retryable_node_error(LookupError("Agent 不存在")) is False


def test_workflow_agent_roles_receive_cost_aware_tool_policies():
    engine = WorkflowEngine()

    assert engine.agent_node_policy_preset("综述提纲规划") == "planning"
    assert engine.agent_node_policy_preset("前沿文献检索") == "research"
    assert engine.agent_node_policy_preset("SCI 综述撰写") == "writing"
    assert engine.agent_node_policy_preset("学术质量评审") == "review"
    assert engine.agent_node_policy_preset("执行项目测试") == "balanced"

    planning = AgentToolPolicy.resolve(
        engine.agent_node_tool_policy("综述提纲规划", {"tool_policy": "auto"})
    )
    research = AgentToolPolicy.resolve(
        engine.agent_node_tool_policy("前沿文献检索", {"tool_policy": "auto"})
    )
    assert planning.max_calls == 0
    assert planning.allow_mcp is False
    assert research.allowed_tools == frozenset({"web_research"})
    assert research.allow_quality_review is False
    assert engine.agent_node_rag_policy("综述提纲规划")["mode"] == "agent"
    assert engine.agent_node_rag_policy("SCI 综述撰写")["mode"] == "off"
    assert engine.agent_node_rag_policy("学术质量评审")["query_rewrite"] is False


def test_tool_results_are_bounded_without_losing_head_and_tail():
    payload = {"status": "completed", "tool": "read_file", "content": "A" * 9000 + "TAIL"}
    compacted = AgentEngine._tool_result_for_model(payload, 1800)

    assert len(compacted) < 2200
    assert '"truncated": true' in compacted
    assert "TAIL" in compacted


def test_corrupted_workflow_prompt_is_detected_and_replaced_with_utf8_default():
    engine = WorkflowEngine()
    assert engine.prompt_looks_corrupted("?" * 40 + "{{input.task}}") is True
    repaired = engine.default_agent_node_prompt("综述提纲规划")
    assert "可执行提纲" in repaired
    assert "不要读取本地文件" in repaired
    assert "只输出完整修订稿" in engine.default_agent_node_prompt("论文修订")


def test_academic_workflow_quality_gate_rejects_truncated_unverified_review():
    task = {"task": "Write an English literature review using 40 papers about mesh quality"}
    truncated = {
        "result": "# Mesh Quality Review\n\n## Abstract\n\nDraft.\n\n## Introduction\n\nText.\n\n## 2.2 Metrics\n\n$\\k"
    }
    issues = WorkflowEngine._delivery_quality_issues(task, truncated, {"nodes": {}})

    assert any("参考文献" in issue for issue in issues)
    assert any("40" in issue for issue in issues)
    assert any("截断" in issue for issue in issues)
    assert any("真实联网" in issue for issue in issues)


def test_web_research_honors_requested_academic_source_count():
    service = WebResearchService()

    assert service.requested_source_count("请检索并纳入 40 篇文献完成综述") == 40
    assert service.requested_source_count("review 120 papers") == 80
    assert service.requested_source_count("普通调研") == 12


def test_web_research_extracts_short_subject_and_constraints_from_workflow_prompt():
    service = WebResearchService()
    task = """【用户原始意图】
围绕网格质量评估给一份综述
【运行前已确认的执行要求】
- 文献规模：40篇
- 文献时间范围：近 10 年
【当前工作流节点】
前沿文献检索：检索可追溯来源并形成证据表。
"""

    assert service.research_subject(task) == "围绕网格质量评估给一份综述"
    assert service.explicit_source_count(task) == 40
    assert service.requested_year_range(task) == (date.today().year - 10, date.today().year)
    queries = service.query_variants(task)
    assert queries[0] == '"mesh quality assessment" CFD finite element numerical simulation'
    assert len(queries) == 4
    assert all("【" not in query and len(query) < 100 for query in queries)


def test_hpe_research_ignores_upstream_mesh_scope_and_year_contamination():
    service = WebResearchService()
    task = """【用户原始意图】
新建一个工作流，帮我调查人体姿态估计相关论文，并写为SCI综述
【运行前已确认的执行要求】
- 目标文献数：30篇
- 文献时间范围：近 10 年
【当前工作流节点】
HPE文献检索Agent
【本节点收到的输入】
上游提纲错误提到了 2014—2024 年的 2D structured mesh quality metrics、Jacobian 和 CFD。
"""

    assert service.research_request(task).endswith("- 文献时间范围：近 10 年")
    assert service.normalized_research_subject(service.research_subject(task)) == "人体姿态估计"
    assert service.explicit_source_count(task) == 30
    assert service.requested_year_range(task) == (date.today().year - 10, date.today().year)
    assert service.mesh_domain(service.research_subject(task)) is None
    assert service.query_variants(task)[0] == '"human pose estimation"'

    ranked = service._rank_results(
        task,
        [
            {
                "title": "Transformer Methods for Human Pose Estimation",
                "description": "A recent study of 2D and 3D human keypoint estimation.",
                "url": "https://doi.org/10.1000/hpe-recent",
                "doi": "10.1000/hpe-recent",
                "source": "Crossref",
                "published_year": date.today().year - 2,
            },
            {
                "title": "Jacobian metrics for structured computational meshes",
                "description": "CFD mesh quality and numerical simulation.",
                "url": "https://doi.org/10.1000/mesh",
                "doi": "10.1000/mesh",
                "source": "Crossref",
                "published_year": date.today().year - 1,
            },
        ],
    )

    assert [item["doi"] for item in ranked] == ["10.1000/hpe-recent"]


def test_academic_quality_gate_treats_positive_source_shortfall_as_warning():
    references = "\n".join(
        f"[{index}] Author. Verified HPE study {index}. https://doi.org/10.1234/hpe.{index}"
        for index in range(1, 13)
    )
    document = (
        "# Human Pose Estimation Review\n\n"
        "## Abstract\n\nA complete abstract.\n\n"
        "## 1. Introduction\n\nA complete introduction.\n\n"
        "## 2. Evidence synthesis\n\n"
        + ("Evidence-based synthesis of verified HPE studies. " * 220)
        + "\n\n## 8. Conclusion\n\nA complete conclusion.\n\n"
        "## References\n\n" + references
    )
    task = {"task": "Write an English literature review targeting about 30 papers about HPE"}
    context = {"nodes": {"research": {"research": {"source_count": 12}}}}

    assert WorkflowEngine._delivery_quality_issues(task, {"result": document}, context) == []
    warnings = WorkflowEngine._delivery_quality_warnings(task, {"result": document}, context)
    assert any("优先目标约 30" in item and "实际列出 12" in item for item in warnings)
    assert any("实际取得 12" in item and "未使用虚构文献" in item for item in warnings)


def test_academic_research_extracts_human_pose_topic_from_workflow_instruction():
    service = WebResearchService()
    task = "新建一个工作流，人体姿态估计相关论文，并写为SCI"

    assert service.normalized_research_subject(service.research_subject(task)) == "人体姿态估计"
    queries = service.query_variants(task)
    assert queries == [
        '"human pose estimation"',
        '"human pose estimation" survey',
        '"human pose estimation" deep learning transformer 2D 3D',
        '"human pose estimation" benchmark dataset evaluation',
    ]
    assert all("工作流" not in query and "SCI" not in query and "论文" not in query for query in queries)

    ranked = service._rank_results(
        task,
        [
            {
                "title": "Human Pose Estimation with Spatial Transformers",
                "description": "A benchmark study for 2D and 3D pose estimation.",
                "url": "https://doi.org/10.1000/hpe",
                "source": "Crossref",
            },
            {
                "title": "Scientific workflow scheduling for cloud systems",
                "description": "Workflow orchestration and scheduling.",
                "url": "https://doi.org/10.1000/workflow",
                "source": "Crossref",
            },
            {
                "title": "Camera Pose Estimation for Autonomous Navigation",
                "description": "Six-degree-of-freedom visual localization.",
                "url": "https://doi.org/10.1000/camera-pose",
                "source": "Crossref",
            },
        ],
    )
    assert [item["title"] for item in ranked] == [
        "Human Pose Estimation with Spatial Transformers"
    ]
    assert ranked[0]["matched_concepts"] == ["pose_estimation", "human_body"]


def test_crossref_query_uses_only_explicit_quoted_concepts():
    service = WebResearchService()

    assert service.crossref_query(
        '"structured grid quality" finite volume solver evaluation'
    ) == "structured grid quality"
    assert service.crossref_query(
        '"human pose estimation" deep learning transformer 2D 3D'
    ) == "human pose estimation"
    assert service.crossref_query("mesh quality CFD") == "mesh quality CFD"


def test_computational_mesh_research_excludes_visual_and_medical_namesakes():
    service = WebResearchService()
    task = "数值计算网格质量评估近 5 年文献综述"
    results = [
        {
            "title": "Mesh quality indicators for finite element numerical simulation",
            "url": "https://doi.org/10.1000/numerical",
            "doi": "10.1000/numerical",
            "source": "Crossref",
            "description": "Jacobian, skewness and discretization error",
            "published_year": date.today().year - 1,
        },
        {
            "title": "No-reference textured mesh visual quality assessment",
            "url": "https://doi.org/10.1000/visual",
            "doi": "10.1000/visual",
            "source": "Crossref",
            "description": "perceptual multimedia compression",
            "published_year": date.today().year,
        },
        {
            "title": "Quality assessment for surgical mesh complications",
            "url": "https://doi.org/10.1000/medical",
            "doi": "10.1000/medical",
            "source": "Crossref",
            "description": "hernia and pelvic surgery",
            "published_year": date.today().year,
        },
    ]

    ranked = service._rank_results(task, results)

    assert [item["doi"] for item in ranked] == ["10.1000/numerical"]
    assert "computational_mesh" in ranked[0]["matched_concepts"]


def test_human_verification_only_accepts_non_login_scholar_cookies():
    service = WebResearchService()
    verification_id = service._begin_verification(
        provider="Google Scholar",
        url="https://scholar.google.com/scholar?q=mesh",
        query="mesh quality",
    )

    result = service.complete_verification(
        verification_id,
        approved=True,
        url="https://scholar.google.com/scholar?q=mesh",
        cookies=[
            {"name": "GSP", "value": "captcha-session"},
            {"name": "SID", "value": "must-not-leave-browser"},
        ],
    )

    assert result["cookie_names"] == ["GSP"]
    assert service._scholar_cookie_header == "GSP=captcha-session"


def test_academic_quality_gate_accepts_numbered_and_bold_markdown_sections():
    references = "\n".join(
        f"[{index}] Author. Verified study {index}. https://doi.org/10.1234/mesh.{index}"
        for index in range(1, 41)
    )
    document = (
        "# Mesh Quality Review\n\n"
        "**Abstract**\n\nA complete abstract.\n\n"
        "## 1. Introduction\n\nA complete introduction.\n\n"
        "## 2. Evidence synthesis\n\n"
        + ("Evidence-based synthesis of verified mesh-quality studies. " * 220)
        + "\n\n## 8. Conclusion\n\nA complete conclusion.\n\n"
        "## References\n\n" + references
    )
    task = {"task": "Write an English literature review using 40 papers about mesh quality"}
    context = {"nodes": {"research": {"research": {"source_count": 40}}}}

    assert WorkflowEngine._delivery_quality_issues(task, {"result": document}, context) == []


def test_web_research_context_keeps_all_citations_inside_budget():
    service = WebResearchService()
    sources = [
        {
            "title": f"Paper {index}",
            "url": f"https://example.org/paper/{index}",
            "source": "Crossref",
            "content": "evidence " * 500,
        }
        for index in range(1, 41)
    ]

    context = service.context(sources, char_limit=16_000)

    assert len(context) <= 16_000
    assert "[1] Paper 1" in context
    assert "[40] Paper 40" in context


def test_research_intent_wins_over_generic_execute_wording():
    intent = intent_service.classify("请执行前沿文献检索并分析近十年论文")

    assert intent.category == "web_research"
    assert "web_research" in intent.required_capabilities


def test_workflow_legacy_condition_expression_is_evaluated_without_exec():
    assert _condition_expression("true == true") is True
    assert _condition_expression("3 >= 2") is True
    assert _condition_expression("'PASS' == 'REVISE'") is False


def test_workflow_runtime_control_supports_pause_guidance_resume_and_interrupt():
    async def exercise():
        engine = WorkflowEngine()
        control = WorkflowControl("test-run")
        engine.controls[control.run_id] = control

        paused = await engine.control(control.run_id, "pause")
        guided = await engine.control(control.run_id, "guide", "优先核验引用")
        resumed = await engine.control(control.run_id, "resume")
        interrupted = await engine.control(control.run_id, "interrupt")

        assert paused["status"] == "pausing"
        assert guided["status"] == "guided"
        assert control.guidance == ["优先核验引用"]
        assert resumed["status"] == "running"
        assert interrupted["status"] == "interrupting"
        assert control.interrupted is True
        assert control.gate.is_set()

    asyncio.run(exercise())


def test_knowledge_chunking_has_overlap_safe_output():
    content = "第一段。" * 200 + "\n\n" + "第二段。" * 200
    chunks = chunk_text(content, chunk_size=200, overlap=20)
    assert len(chunks) > 2
    assert all(len(item) <= 200 for item in chunks)


def test_secret_store_round_trip_and_ciphertext_is_not_plaintext():
    ciphertext = secret_store.encrypt("private-key")
    assert ciphertext != "private-key"
    assert secret_store.decrypt(ciphertext) == "private-key"


def test_intent_classifier_routes_commands_knowledge_and_vague_requests():
    command = intent_service.classify("请在本地项目执行 `npm run build`")
    knowledge = intent_service.classify("从知识库检索科研伦理规范")
    vague = intent_service.classify("看看")

    assert command.category == "command_execution"
    assert "exec" in command.required_capabilities
    assert knowledge.category == "knowledge_retrieval"
    assert "mcp" in knowledge.required_capabilities
    assert vague.needs_clarification is True
    assert ToolRuntime.plan_local_request("请执行 `npm run build`") == {
        "tool": "exec",
        "arguments": {"command": "npm run build"},
    }
