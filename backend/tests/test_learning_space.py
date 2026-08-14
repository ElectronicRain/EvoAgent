from __future__ import annotations

from backend.app.services.computer_science_ontology import CS2023_UNITS, curriculum_nodes


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


def test_cs2023_ontology_covers_all_areas_and_keeps_subjects_isolated():
    assert len(CS2023_UNITS) == 17
    assert sum(len(items) for items in CS2023_UNITS.values()) == 166
    expected_areas = {
        "network": {"NC"},
        "operating_system": {"OS"},
        "architecture": {"AR"},
        "database": {"DM"},
        "distributed": {"PDC"},
        "security": {"SEC"},
        "algorithms": {"SDF", "AL"},
        "software_engineering": {"SDF", "SE"},
        "rag_agents": {"MSF", "AI"},
        "computer_vision": {"MSF", "AI", "GIT"},
        "graphics_hci": {"GIT", "HCI"},
        "data_science": {"MSF", "DM", "AI"},
    }
    for focus, expected in expected_areas.items():
        nodes = curriculum_nodes([focus], "计算机基础")
        codes = {item["code"] for item in nodes}
        assert {item["knowledge_area"] for item in nodes} == expected
        assert len(codes) == len(nodes)
        assert all(set(item["prerequisites"]) <= codes for item in nodes)
        assert all(item["source_refs"] and item["source_refs"][0]["url"].startswith("https://") for item in nodes)
        assert not any("目标场景迁移" in item["title"] or "综合优化与开放问题" in item["title"] for item in nodes)


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
    assert project["counts"]["nodes"] >= 36
    assert project["counts"]["questions"] == project["counts"]["nodes"]
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
    assert {"spd-web", "se-requirements", "se-validation", "hci-design"} <= web_codes
    assert {"os-purpose", "os-concurrency", "os-process", "os-memory", "os-files"} <= systems_codes
    assert all(code.startswith(("sdf-", "se-", "hci-", "spd-")) for code in web_codes)
    assert all(code.startswith("os-") for code in systems_codes)
    assert len(web_codes ^ systems_codes) >= 6
    assert web_space["project"]["settings"]["personalized"] is True
    assert web_space["project"]["settings"]["content_version"] == 4
    assert web_space["project"]["settings"]["path_granularity"] == "authoritative_knowledge_unit_or_leaf"
    assert web_space["project"]["settings"]["direction_profile"]["signature"] != systems_space["project"]["settings"]["direction_profile"]["signature"]
    assert all("Web 全栈电商系统开发" in item["description"] for item in web_space["nodes"])
    assert all(any(ref.get("granularity") == "knowledge_unit" for ref in item["source_refs"]) for item in web_space["nodes"])
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
    assert {"ai-agents", "ai-nlp", "ai-search"} <= {item["code"] for item in rebuilt_space["nodes"]}
    assert len(rebuilt_space["memories"]) == 1
    assert rebuilt_space["tasks"]
    assert not rebuilt_space["attempts"]


def test_network_direction_uses_complete_network_ontology_without_cross_subject_nodes(client):
    learner = register(client, "network_ontology_learner")
    auth = headers(learner)
    created = client.post(
        "/api/learning-projects",
        headers=auth,
        json={
            "name": "计算机网络系统学习",
            "project_type": "course",
            "description": "从 TCP/IP 分层到路由、可靠传输、网络安全和移动网络。",
            "target": "掌握计算机网络全部核心知识，并能完成协议分析与抓包验证。",
            "current_level": "beginner",
            "target_level": "advanced",
            "weekly_hours": 8,
            "track": "计算机基础",
        },
    )
    assert created.status_code == 201, created.text
    workspace = client.get(
        f"/api/learning-projects/{created.json()['id']}/workspace", headers=auth
    ).json()
    nodes = workspace["nodes"]
    titles = {item["title"] for item in nodes}
    assert len(nodes) == 47
    assert all(item["code"].startswith("nc-") for item in nodes)
    assert all(item["domain"] == "网络与通信" for item in nodes)
    assert {
        "TCP/IP 分层与各层职责",
        "HTTP 等应用层协议",
        "TCP 状态、可靠传输与性能",
        "BGP 与自治系统间路由",
        "IEEE 802.11 Wi-Fi",
        "TLS 与安全信道",
        "蜂窝网络与 4G/5G 基本机制",
        "软件定义网络与网络虚拟化",
    } <= titles
    assert not any("操作系统" in title or "程序的装入" in title or "目标场景迁移" in title for title in titles)
    metadata = [
        next(ref for ref in item["source_refs"] if ref.get("type") == "learning_path_metadata")
        for item in nodes
    ]
    assert {item["knowledge_area"] for item in metadata} == {"NC"}
    assert len({item["knowledge_unit"] for item in metadata}) == 8
    assert {item["ontology_version"] for item in metadata} == {"cs2023-v1"}
    assert {item["granularity"] for item in metadata} == {"authoritative_leaf"}
    ontology = workspace["project"]["settings"]["ontology"]
    assert ontology["knowledge_area_count"] == 1
    assert ontology["knowledge_unit_count"] == 8
    assert ontology["node_count"] == 47
