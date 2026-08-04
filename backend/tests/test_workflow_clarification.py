from __future__ import annotations

import pytest

from backend.app.services.workflow_clarification import workflow_clarification_service


def question_ids(result: dict) -> set[str]:
    return {item["id"] for item in result["questions"]}


def test_vague_review_requests_key_decisions_before_run():
    result = workflow_clarification_service.analyze("生成一篇人工智能教育领域综述")

    assert result["required"] is True
    assert result["task_type"] == "literature_review"
    assert {
        "output_language",
        "literature_count",
        "literature_time_range",
        "review_method",
        "review_focus",
    } == question_ids(result)


def test_stock_research_asks_market_specific_questions_before_orchestration():
    result = workflow_clarification_service.analyze(
        "我想研究后面A股哪只股票会大涨，哪些板块比较看好，"
        "给我建议，价格区间在50元以下的，有潜力的股票和建议",
        phase="orchestration",
    )

    assert result["task_type"] == "financial_research"
    assert result["task_type_label"] == "证券市场研究"
    assert "price_ceiling" not in question_ids(result)
    assert {
        "investment_horizon",
        "risk_preference",
        "analysis_style",
        "candidate_count",
        "workflow_strategy",
    } == question_ids(result)


def test_stock_software_development_is_not_misclassified_as_market_research():
    result = workflow_clarification_service.analyze("开发一个A股行情分析系统")

    assert result["task_type"] == "implementation"
    assert "runtime_platform" in question_ids(result)
    assert "risk_preference" not in question_ids(result)


def test_resolved_stock_research_adds_current_data_and_risk_constraints():
    result = workflow_clarification_service.resolve(
        "研究A股50元以下的潜力股票和行业板块",
        {
            "investment_horizon": "medium",
            "risk_preference": "balanced",
            "analysis_style": "comprehensive",
            "candidate_count": 8,
        },
    )

    assert "研究周期：中期（1—6 个月）" in result["resolved_task"]
    assert "行情与资料基准日期" in result["resolved_task"]
    assert "不得承诺某只股票必涨" in result["resolved_task"]
    assert "非投资建议声明" in result["resolved_task"]


def test_explicit_review_does_not_repeat_answers_already_in_task():
    result = workflow_clarification_service.analyze(
        "用中文撰写一篇系统综述，检索近5年不少于30篇文献，重点比较生成式人工智能在高校教学中的应用效果。"
    )

    assert result["required"] is False
    assert result["questions"] == []


def test_ambiguous_mesh_review_asks_for_the_research_domain():
    result = workflow_clarification_service.analyze("生成一篇网格质量评估领域综述")

    assert "mesh_research_domain" in question_ids(result)
    question = next(item for item in result["questions"] if item["id"] == "mesh_research_domain")
    assert question["default"] == "computational"
    assert {item["value"] for item in question["options"]} == {
        "computational",
        "visual",
        "comparative",
    }


def test_explicit_computational_mesh_review_does_not_ask_domain_again():
    result = workflow_clarification_service.analyze(
        "用中文撰写一篇数值计算网格的系统综述，检索近5年不少于30篇文献，重点比较 CFD 与有限元质量指标。"
    )

    assert "mesh_research_domain" not in question_ids(result)


def test_data_analysis_and_implementation_use_different_requirements():
    analysis = workflow_clarification_service.analyze("分析一下销售数据")
    implementation = workflow_clarification_service.analyze("帮我开发一个库存管理功能")

    assert analysis["task_type"] == "data_analysis"
    assert {"data_source", "analysis_goal", "analysis_deliverable"} == question_ids(analysis)
    assert implementation["task_type"] == "implementation"
    assert {"runtime_platform", "acceptance_criteria", "change_scope"} == question_ids(
        implementation
    )


@pytest.mark.parametrize(
    ("task", "task_type"),
    [
        (
            "使用已上传的 CSV 分析销售额下降原因，并输出包含趋势图的分析报告。",
            "data_analysis",
        ),
        (
            "在当前 Vue 和 FastAPI 项目中以最小改动实现导出功能，确保自动化测试通过。",
            "implementation",
        ),
        (
            "围绕产品留存下降原因开展近1年的深度调研，重点比较主要竞品并交付结论。",
            "research",
        ),
    ],
)
def test_explicit_cross_domain_tasks_do_not_trigger_redundant_questions(task, task_type):
    result = workflow_clarification_service.analyze(task)

    assert result["task_type"] == task_type
    assert result["questions"] == []


def test_new_canvas_orchestration_has_independent_intent_and_strategy_question():
    result = workflow_clarification_service.analyze(
        "为新生设计一套校园适应支持方案",
        workflow_name="未命名协作工作流",
        definition={"nodes": [{"id": "input"}, {"id": "output"}], "edges": []},
        phase="orchestration",
    )

    assert result["phase"] == "orchestration"
    assert result["intent"]["objective"] == "为新生设计一套校园适应支持方案"
    assert result["intent"]["known_context"]["existing_node_count"] == 2
    assert "workflow_strategy" in question_ids(result)
    assert "编排侧重" in result["intent"]["missing_decisions"]


def test_confirmed_answers_are_added_to_executable_task():
    result = workflow_clarification_service.resolve(
        "生成一篇人工智能教育领域综述",
        {
            "output_language": "en",
            "literature_count": 40,
            "literature_time_range": "recent_10_years",
            "review_method": "systematic",
            "review_focus": "比较课堂应用效果、风险与评价指标",
        },
    )

    assert result["confirmed"] is True
    assert result["required"] is False
    assert "输出语言：英文" in result["resolved_task"]
    assert "目标文献数：40篇" in result["resolved_task"]
    assert "综述类型：系统综述" in result["resolved_task"]
    assert "不得擅自缩减范围" in result["resolved_task"]
    assert "文献数量是优先检索目标" in result["resolved_task"]
    assert "实际数量并继续完成交付" in result["resolved_task"]


def test_confirmed_number_is_range_checked():
    with pytest.raises(ValueError, match="目标文献数"):
        workflow_clarification_service.resolve(
            "生成一篇人工智能教育领域综述",
            {
                "output_language": "zh-CN",
                "literature_count": 2,
                "literature_time_range": "recent_5_years",
                "review_method": "narrative",
                "review_focus": "应用",
            },
        )


def test_workflow_clarification_api_analyzes_and_resolves(client):
    payload = {
        "task": "生成一篇人工智能教育领域综述",
        "workflow_name": "综述生成工作流",
        "workflow_description": "检索文献并撰写综述",
        "definition": {"nodes": [{"id": "research"}], "edges": []},
    }
    analyzed = client.post("/api/workflow-clarification", json=payload)

    assert analyzed.status_code == 200
    assert analyzed.json()["required"] is True
    assert analyzed.json()["definition_node_count"] == 1

    resolved = client.post(
        "/api/workflow-clarification",
        json={
            **payload,
            "confirmed": True,
            "answers": {
                "output_language": "zh-CN",
                "literature_count": 30,
                "literature_time_range": "recent_5_years",
                "review_method": "narrative",
                "review_focus": "关键技术、证据质量与未来趋势",
            },
        },
    )

    assert resolved.status_code == 200
    assert resolved.json()["confirmed"] is True
    assert "运行前已确认的执行要求" in resolved.json()["resolved_task"]
