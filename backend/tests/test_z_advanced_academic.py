from __future__ import annotations


def register(client, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "display_name": username, "password": "advanced-pass-2026"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(account: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {account['token']}"}


def test_personalized_diagnostic_path_companion_and_traceable_qa(client):
    account = register(client, "advanced_learner")
    headers = auth(account)
    created = client.post(
        "/api/learning-projects",
        headers=headers,
        json={
            "name": "Python 算法与复杂度提升",
            "project_type": "skill",
            "discipline": "计算机科学",
            "description": "围绕排序、查找、数据结构和复杂度证明完成专项学习。",
            "target": "能推导复杂度、调试实现并解释算法适用边界。",
            "current_level": "foundation",
            "target_level": "advanced",
            "weekly_hours": 7,
            "track": "程序设计",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    diagnostic = client.get(
        f"/api/learning-projects/{project_id}/diagnostic", headers=headers
    )
    assert diagnostic.status_code == 200, diagnostic.text
    diagnosis = diagnostic.json()
    assert set(diagnosis["dimensions"]) == {
        "knowledge_mastery", "practice_accuracy", "task_progress",
        "mistake_correction", "learning_engagement",
    }
    assert 0 <= diagnosis["overall_score"] <= 100
    assert diagnosis["evidence_counts"]["nodes"] >= 6
    assert diagnosis["gaps"]
    assert diagnosis["recommended_actions"]

    path_response = client.get(
        f"/api/learning-projects/{project_id}/personalized-path", headers=headers
    )
    assert path_response.status_code == 200, path_response.text
    path = path_response.json()
    assert len(path["nodes"]) == diagnosis["evidence_counts"]["nodes"]
    assert path["goal"] == "能推导复杂度、调试实现并解释算法适用边界。"
    assert path["target_depth"] == 5
    assert 1 <= path["active_depth"] <= path["target_depth"]
    assert path["next_checkpoint"]
    assert {item["granularity"] for item in path["nodes"]} == {"micro"}
    assert all(item["depth_level"] >= 1 for item in path["nodes"])
    assert all(0 <= item["goal_alignment"] <= 100 for item in path["nodes"])
    assert all(item["adaptation_reason"] for item in path["nodes"])
    assert all("depth_level" in stage for stage in path["stages"])
    assert path["current_node_id"]
    assert {item["state"] for item in path["nodes"]} <= {
        "mastered", "current", "ready", "locked"
    }
    assert any(item["resources"] for item in path["nodes"])
    assert path["edges"]

    replanned = client.post(
        f"/api/learning-projects/{project_id}/personalized-path/replan",
        headers=headers,
        json={"regenerate_plan": True, "focus": [diagnosis["gaps"][0]["code"]]},
    )
    assert replanned.status_code == 200, replanned.text
    assert replanned.json()["tasks"]
    assert replanned.json()["path"]["current_node_id"] == path["current_node_id"]

    companion = client.post(
        f"/api/learning-projects/{project_id}/companion/session",
        headers=headers,
        json={"minutes": 50, "mood": "tired", "goal": "完成复杂度推导和一个验证样例"},
    )
    assert companion.status_code == 200, companion.text
    session = companion.json()
    assert sum(step["minutes"] for step in session["steps"]) == 50
    assert len(session["steps"]) == 3
    assert "任务缩小" in session["message"]
    assert session["diagnostic_snapshot"]["top_gap"]

    workspace = client.get(
        f"/api/learning-projects/{project_id}/workspace", headers=headers
    ).json()
    formula = client.post(
        f"/api/learning-projects/{project_id}/tutor",
        headers=headers,
        json={
            "message": "请逐步推导归并排序为什么是 O(n log n)，并说明公式中每个量。",
            "mode": "explain",
            "knowledge_node_id": workspace["nodes"][0]["id"],
        },
    )
    assert formula.status_code == 200, formula.text
    answer = formula.json()
    assert answer["metadata"]["question_type"] == "formula_derivation"
    assert len(answer["metadata"]["guidance_protocol"]) == 5
    assert answer["metadata"]["source_traceable"] is True
    assert answer["citations"]

    preference = client.patch(
        f"/api/learning-projects/{project_id}",
        headers=headers,
        json={"settings": {"learning_preferences": {"mentor_style": "socratic", "session_minutes": 50}}},
    )
    assert preference.status_code == 200, preference.text
    personal = client.get(
        f"/api/learning-projects/{project_id}/personal-space", headers=headers
    )
    assert personal.status_code == 200, personal.text
    assert personal.json()["preferences"]["session_minutes"] == 50
    assert personal.json()["direction_profile"]["signature"]


def test_frontier_tracking_dataset_profile_and_scipilot_figure_pipeline(client):
    account = register(client, "advanced_researcher")
    headers = auth(account)
    project_response = client.post(
        "/api/research-projects",
        headers=headers,
        json={
            "name": "大模型代码调试反馈前沿研究",
            "discipline": "计算机科学",
            "description": "分析自适应反馈对代码调试表现的影响。",
            "research_question": "自适应大模型反馈如何影响程序调试正确率与完成时间？",
            "expected_outcome": "可复现论文与矢量图表",
            "citation_style": "IEEE",
            "language": "zh-CN",
        },
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]

    papers = [
        ("Adaptive LLM Feedback for Programming Debugging", 2026, "LLM", "debugging"),
        ("Large Language Models in Programming Education", 2025, "LLM", "education"),
        ("Debugging Transfer with Adaptive Feedback", 2025, "adaptive", "debugging"),
        ("Explainable Feedback for Novice Programmers", 2024, "feedback", "education"),
    ]
    source_ids = []
    for index, (title, year, left, right) in enumerate(papers):
        response = client.post(
            f"/api/research-projects/{project_id}/literature",
            headers=headers,
            json={
                "title": title,
                "authors": f"Author {index}; Coauthor",
                "year": year,
                "doi": f"10.1000/frontier-{index}",
                "url": f"https://doi.org/10.1000/frontier-{index}",
                "source": "Crossref",
                "abstract": "A traceable computer science study.",
                "status": "included",
                "credibility": 90 - index,
                "tags": [left, right, "programming education"],
            },
        )
        assert response.status_code == 201, response.text
        source_ids.append(response.json()["id"])

    frontier = client.post(
        f"/api/research-projects/{project_id}/frontier/track",
        headers=headers,
        json={"query": "LLM debugging feedback", "recent_years": 3, "target_count": 20, "refresh": False},
    )
    assert frontier.status_code == 201, frontier.text
    snapshot = frontier.json()
    assert len(snapshot["sources"]) == 4
    assert set(source_ids) == {item["id"] for item in snapshot["sources"]}
    assert snapshot["hot_topics"]
    assert snapshot["nodes"] and snapshot["edges"]
    assert len(snapshot["timeline"]) == 3
    assert "项目内证据推断" in snapshot["methodology"]
    assert snapshot["refresh"]["requested"] is False
    listed = client.get(f"/api/research-projects/{project_id}/frontier", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["metadata"]["traceable"] is True

    csv_data = (
        "group,accuracy,time_seconds\n"
        "control,0.62,42\ncontrol,0.66,39\ncontrol,0.64,41\ncontrol,,44\n"
        "adaptive,0.79,31\nadaptive,0.83,28\nadaptive,0.81,30\nadaptive,0.85,27\n"
    ).encode("utf-8")
    uploaded = client.post(
        f"/api/research-projects/{project_id}/data-assets/upload",
        headers=headers,
        files={"file": ("debugging-results.csv", csv_data, "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    dataset = uploaded.json()
    assert dataset["profile"]["rows"] == 8
    assert dataset["profile"]["numeric_fields"] == ["accuracy", "time_seconds"]
    assert dataset["profile"]["categorical_fields"] == ["group"]
    assert dataset["profile"]["correlations"]
    assert dataset["quality_warnings"]
    assert any("相关不等于因果" in item for item in dataset["insights"])
    assert dataset["skill"]["name"] == "scipilot-figure-skill"

    figure = client.post(
        f"/api/research-projects/{project_id}/figures",
        headers=headers,
        json={
            "dataset_id": dataset["id"],
            "argument": "比较不同反馈组的代码调试正确率分布",
            "chart_type": "pie",
            "x": "group",
            "y": "accuracy",
            "title": "自适应反馈组与对照组的调试正确率",
            "journal": "ieee",
        },
    )
    assert figure.status_code == 201, figure.text
    result = figure.json()
    assert result["spec"]["chart_type"] == "strip"
    assert result["spec"]["warnings"]
    assert result["spec"]["colorblind_safe"] is True
    assert result["spec"]["vector"] is True
    assert result["svg"].startswith("<svg")
    assert 'fill="#ffffff"' in result["svg"]
    assert "SciPilot QA" in result["svg"]

    svg = client.get(
        f"/api/research-projects/{project_id}/figures/{result['id']}/svg", headers=headers
    )
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.startswith("<svg")

    assets = client.get(f"/api/research-projects/{project_id}/data-assets", headers=headers)
    assert assets.status_code == 200
    kinds = {item["kind"] for item in assets.json()}
    assert kinds == {"research-dataset", "publication-figure"}
    dataset_item = next(item for item in assets.json() if item["kind"] == "research-dataset")
    assert "records" not in dataset_item["payload"]
    assert len(dataset_item["payload"]["sample"]) == 8
