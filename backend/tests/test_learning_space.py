from __future__ import annotations


def register(client, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": username,
            "password": "learning-pass-2026",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(result: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {result['token']}"}


def test_computer_subject_pack_and_complete_learning_loop(client):
    learner = register(client, "computer_learner")
    outsider = register(client, "learning_outsider")
    auth = headers(learner)

    pack_response = client.get(
        "/api/learning-subject-packs/computer-science", headers=auth
    )
    assert pack_response.status_code == 200, pack_response.text
    pack = pack_response.json()
    assert pack["group"]["name"] == "计算机科学学科包"
    assert len(pack["knowledge_bases"]) == 3
    assert len(pack["agents"]) == 6
    assert len(pack["workflows"]) == 2
    assert all(base["document_count"] >= 2 for base in pack["knowledge_bases"])

    created = client.post(
        "/api/learning-projects",
        headers=auth,
        json={
            "name": "计算机基础系统学习",
            "project_type": "course",
            "description": "以可追溯资料完成计算机核心知识学习。",
            "target": "完成核心知识、练习、错题订正和综合评测。",
            "current_level": "beginner",
            "target_level": "proficient",
            "weekly_hours": 6,
            "track": "计算机基础",
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    assert project["counts"]["nodes"] == 12
    assert project["counts"]["questions"] == 12
    assert len(project["agent_bindings"]) == 6
    assert len(project["workflow_bindings"]) == 2
    assert len(project["knowledge_base_ids"]) == 3

    forbidden = client.get(
        f"/api/learning-projects/{project_id}", headers=headers(outsider)
    )
    assert forbidden.status_code == 403

    plan_response = client.post(
        f"/api/learning-projects/{project_id}/plan/generate",
        headers=auth,
        json={"regenerate": False, "focus": []},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert len(plan) == 36
    assert {item["module"] for item in plan} == {"learn", "practice", "review"}
    assert all(25 <= item["duration_minutes"] <= 45 for item in plan)

    completed = client.patch(
        f"/api/learning-projects/{project_id}/tasks/{plan[0]['id']}",
        headers=auth,
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    assert completed.json()["progress"] == 100

    workspace = client.get(
        f"/api/learning-projects/{project_id}/workspace", headers=auth
    )
    assert workspace.status_code == 200, workspace.text
    data = workspace.json()
    node_id = data["nodes"][0]["id"]
    tutor = client.post(
        f"/api/learning-projects/{project_id}/tutor",
        headers=auth,
        json={
            "message": "时间复杂度 O(n log n) 应该怎样理解？",
            "mode": "socratic",
            "knowledge_node_id": node_id,
        },
    )
    assert tutor.status_code == 200, tutor.text
    tutor_result = tutor.json()
    assert tutor_result["role"] == "assistant"
    assert "计算机基础系统学习" in tutor_result["content"]
    assert tutor_result["metadata"]["source_traceable"] is True
    assert tutor_result["citations"]
    assert all(item["title"] and item["source"] for item in tutor_result["citations"])
    assert any("算法" in item["title"] for item in tutor_result["citations"])

    data = client.get(
        f"/api/learning-projects/{project_id}/workspace", headers=auth
    ).json()
    question = data["questions"][0]
    correct_answer = question["answer"]["value"]
    correct = client.post(
        f"/api/learning-projects/{project_id}/attempts",
        headers=auth,
        json={"question_id": question["id"], "answer": correct_answer},
    )
    assert correct.status_code == 201, correct.text
    assert correct.json()["attempt"]["score"] == 100
    assert correct.json()["attempt"]["is_correct"] is True
    assert correct.json()["mistake"] is None

    second_question = data["questions"][1]
    wrong = client.post(
        f"/api/learning-projects/{project_id}/attempts",
        headers=auth,
        json={"question_id": second_question["id"], "answer": "明显错误的答案"},
    )
    assert wrong.status_code == 201, wrong.text
    wrong_result = wrong.json()
    assert wrong_result["attempt"]["score"] == 0
    assert wrong_result["attempt"]["error_type"] == "concept_or_boundary"
    assert wrong_result["mistake"]["status"] == "open"
    assert wrong_result["mistake"]["next_review_at"] is not None

    reviewed = client.patch(
        f"/api/learning-projects/{project_id}/mistakes/{wrong_result['mistake']['id']}",
        headers=auth,
        json={"status": "reviewing", "reviewed": True, "correction": "互斥用于保护临界区。"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review_count"] == 1

    memory = client.post(
        f"/api/learning-projects/{project_id}/memories",
        headers=auth,
        json={
            "category": "method",
            "content": "分析复杂度时同时记录输入规模、增长率和边界条件。",
            "source_type": "tutor",
            "confidence": 0.9,
        },
    )
    assert memory.status_code == 201

    assessment = client.post(
        f"/api/learning-projects/{project_id}/assessments",
        headers=auth,
        json={"period": "current"},
    )
    assert assessment.status_code == 201, assessment.text
    report = assessment.json()
    assert 0 <= report["overall_score"] <= 100
    assert report["metrics"]["practice_accuracy"] == 50
    assert report["metrics"]["attempt_count"] == 2
    assert report["metrics"]["completed_tasks"] == 1
    assert report["recommendations"]


def test_each_learning_direction_generates_distinct_content_and_can_rebuild(client):
    learner = register(client, "direction_specific_learner")
    auth = headers(learner)

    def create(payload: dict) -> dict:
        response = client.post("/api/learning-projects", headers=auth, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    web = create({
        "name": "Web 全栈电商系统开发",
        "project_type": "project",
        "description": "使用 Vue、后端 API、SQL 和身份认证完成可部署电商系统。",
        "target": "交付商品、订单、用户模块和自动化测试。",
        "current_level": "foundation",
        "target_level": "proficient",
        "weekly_hours": 8,
        "track": "程序设计",
    })
    systems = create({
        "name": "操作系统与并发编程备考",
        "project_type": "exam",
        "description": "系统学习进程线程、同步互斥、死锁和虚拟内存。",
        "target": "完成系统考点覆盖、真题训练和错题闭环。",
        "current_level": "beginner",
        "target_level": "advanced",
        "weekly_hours": 6,
        "track": "计算机基础",
    })

    web_space = client.get(f"/api/learning-projects/{web['id']}/workspace", headers=auth).json()
    systems_space = client.get(f"/api/learning-projects/{systems['id']}/workspace", headers=auth).json()
    web_codes = {item["code"] for item in web_space["nodes"]}
    systems_codes = {item["code"] for item in systems_space["nodes"]}
    assert {"web-frontend", "backend-service", "auth-security"} <= web_codes
    assert {"operating-systems", "concurrency", "architecture"} <= systems_codes
    assert len(web_codes ^ systems_codes) >= 6
    assert web_space["project"]["settings"]["personalized"] is True
    assert web_space["project"]["settings"]["content_version"] == 2
    assert web_space["project"]["settings"]["direction_profile"]["signature"] != systems_space["project"]["settings"]["direction_profile"]["signature"]
    assert all("Web 全栈电商系统开发" in item["description"] for item in web_space["nodes"])
    assert all("Web 全栈电商系统开发" in item["prompt"] for item in web_space["questions"])
    assert all("操作系统与并发编程备考" in item["prompt"] for item in systems_space["questions"])

    plan_response = client.post(
        f"/api/learning-projects/{web['id']}/plan/generate",
        headers=auth,
        json={"regenerate": False, "focus": []},
    )
    assert plan_response.status_code == 200, plan_response.text
    assert all("Web 全栈电商系统开发" in item["title"] for item in plan_response.json())
    assert all("交付商品、订单、用户模块" in item["description"] for item in plan_response.json())

    memory = client.post(
        f"/api/learning-projects/{web['id']}/memories",
        headers=auth,
        json={"category": "method", "content": "所有接口先写验收条件。", "source_type": "user", "confidence": 1},
    )
    assert memory.status_code == 201
    old_signature = web_space["project"]["settings"]["direction_profile"]["signature"]
    updated = client.patch(
        f"/api/learning-projects/{web['id']}",
        headers=auth,
        json={"target": "增加 RAG 知识库与智能体客服，并量化检索和回答准确率。"},
    )
    assert updated.status_code == 200
    rebuilt = client.post(
        f"/api/learning-projects/{web['id']}/direction/regenerate",
        headers=auth,
        json={"keep_memories": True},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["memories_preserved"] is True
    rebuilt_space = client.get(f"/api/learning-projects/{web['id']}/workspace", headers=auth).json()
    assert rebuilt_space["project"]["settings"]["direction_profile"]["signature"] != old_signature
    assert "rag-agents" in {item["code"] for item in rebuilt_space["nodes"]}
    assert len(rebuilt_space["memories"]) == 1
    assert rebuilt_space["tasks"]
    assert not rebuilt_space["attempts"]
