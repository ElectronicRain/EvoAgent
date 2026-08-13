from __future__ import annotations


def register(client, username: str, display_name: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": display_name,
            "password": "research-pass-2026",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(result: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {result['token']}"}


def test_complete_research_application_and_collaboration(client, monkeypatch):
    from backend.app.services import research_projects as research_module

    async def no_online_endpoint(_db):
        return None

    monkeypatch.setattr(research_module, "latest_chat_endpoint", no_online_endpoint)
    owner = register(client, "research_owner", "科研负责人")
    collaborator = register(client, "research_editor", "共同作者")
    outsider = register(client, "research_outsider", "项目外用户")
    owner_headers, editor_headers = auth(owner), auth(collaborator)

    created = client.post(
        "/api/research-projects",
        headers=owner_headers,
        json={
            "name": "大模型程序设计教育研究",
            "discipline": "计算机科学",
            "description": "研究大模型反馈对程序调试能力的影响。",
            "research_question": "基于错误类型的反馈是否提升本科生调试迁移能力？",
            "expected_outcome": "LaTeX 学术论文",
            "citation_style": "IEEE",
            "language": "zh-CN",
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    assert project["role"] == "owner"

    forbidden = client.get(f"/api/research-projects/{project_id}", headers=auth(outsider))
    assert forbidden.status_code == 403

    member = client.post(
        f"/api/research-projects/{project_id}/members",
        headers=owner_headers,
        json={"username": "research_editor", "role": "editor"},
    )
    assert member.status_code == 201, member.text
    assert member.json()["role"] == "editor"
    assert (
        client.get(f"/api/research-projects/{project_id}", headers=editor_headers).json()["role"]
        == "editor"
    )

    invited = register(client, "research_invited", "邀请码成员")
    invite = client.post(
        f"/api/research-projects/{project_id}/invites",
        headers=owner_headers,
        json={"role": "reviewer", "expires_hours": 24, "max_uses": 2},
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["code"].startswith("EVO-")
    joined = client.post(
        "/api/research-projects/join",
        headers=auth(invited),
        json={"code": invite.json()["code"]},
    )
    assert joined.status_code == 201, joined.text
    assert joined.json()["role"] == "reviewer"
    ledger = client.get(
        f"/api/research-projects/{project_id}/ledger", headers=owner_headers
    )
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["verified"] is True
    assert len(ledger.json()["head_hash"]) == 64
    assert any(
        item["action"] == "member.joined_by_invite"
        for item in ledger.json()["entries"]
    )

    literature = client.post(
        f"/api/research-projects/{project_id}/literature",
        headers=editor_headers,
        json={
            "title": "Large Language Models for Programming Education",
            "authors": "Research Team",
            "year": 2025,
            "doi": "10.1000/research-test",
            "url": "https://doi.org/10.1000/research-test",
            "source": "Crossref",
            "abstract": "A controlled study of feedback and debugging transfer.",
            "status": "included",
            "credibility": 92,
            "tags": ["LLM", "programming education"],
            "notes": "重点方法文献",
        },
    )
    assert literature.status_code == 201, literature.text
    assert literature.json()["tags"] == ["LLM", "programming education"]

    related_literature = client.post(
        f"/api/research-projects/{project_id}/literature",
        headers=editor_headers,
        json={
            "title": "Adaptive Feedback for Debugging Transfer in Programming Education",
            "authors": "Research Team; Second Author",
            "year": 2026,
            "doi": "10.1000/research-related",
            "source": "Crossref",
            "abstract": "Adaptive language model feedback supports debugging and programming transfer.",
            "status": "priority",
            "credibility": 90,
            "tags": ["LLM", "programming education", "debugging"],
        },
    )
    assert related_literature.status_code == 201

    figure = client.post(
        f"/api/research-projects/{project_id}/literature/figure",
        headers=editor_headers,
    )
    assert figure.status_code == 201, figure.text
    assert figure.json()["figure"]["background"] == "#ffffff"
    assert literature.json()["id"] in figure.json()["figure"]["source_ids"]
    assert figure.json()["figure"]["schema_version"] == "2.0"
    assert figure.json()["figure"]["edges"]
    assert figure.json()["figure"]["edges"][0]["relation"] in {
        "共同作者", "主题/方法相似", "同期研究主题", "时间邻近"
    }

    idea = client.post(
        f"/api/research-projects/{project_id}/ideas",
        headers=editor_headers,
        json={
            "title": "错误类型自适应反馈",
            "problem": "通用反馈缺少针对性。",
            "hypothesis": "按错误类型生成反馈可提升迁移测试成绩。",
            "novelty": "把错误分类与教学反馈强度联动。",
            "method": "随机对照实验。",
            "evidence": [{"literature_id": literature.json()["id"]}],
            "scores": {"novelty": 82, "feasibility": 75},
            "status": "validation",
        },
    )
    assert idea.status_code == 201, idea.text
    editable_idea = client.post(
        f"/api/research-projects/{project_id}/ideas",
        headers=editor_headers,
        json={"title": "可编辑待删除 Idea", "problem": "测试卡片完整 CRUD"},
    )
    assert editable_idea.status_code == 201
    edited = client.patch(
        f"/api/research-projects/{project_id}/ideas/{editable_idea.json()['id']}",
        headers=editor_headers,
        json={"title": "已修改 Idea", "status": "validation"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "已修改 Idea"
    deleted = client.delete(
        f"/api/research-projects/{project_id}/ideas/{editable_idea.json()['id']}",
        headers=editor_headers,
    )
    assert deleted.status_code == 204
    project_after_idea = client.get(
        f"/api/research-projects/{project_id}", headers=editor_headers
    ).json()
    assert project_after_idea["counts"]["ideas"] == 1

    settings = client.patch(
        f"/api/research-projects/{project_id}",
        headers=editor_headers,
        json={"settings": {"module_agents": {"ideas": "agent-test"}}},
    )
    assert settings.status_code == 200
    assert settings.json()["settings"]["module_agents"]["ideas"] == "agent-test"
    experiment = client.post(
        f"/api/research-projects/{project_id}/ideas/{idea.json()['id']}/experiment",
        headers=editor_headers,
    )
    assert experiment.status_code == 201, experiment.text
    assert experiment.json()["design"]["random_seed"] == 42
    updated_experiment = client.patch(
        f"/api/research-projects/{project_id}/experiments/{experiment.json()['id']}",
        headers=editor_headers,
        json={
            "status": "completed",
            "result": {"effect_size": 0.42, "p_value": 0.018, "sample_size": 120},
        },
    )
    assert updated_experiment.status_code == 200
    assert updated_experiment.json()["result"]["sample_size"] == 120

    memory = client.post(
        f"/api/research-projects/{project_id}/memories",
        headers=owner_headers,
        json={
            "category": "decision",
            "content": "主要结局指标采用迁移测试正确率。",
            "source_type": "user",
            "confidence": 1,
            "locked": True,
        },
    )
    assert memory.status_code == 201
    skill = client.post(
        f"/api/research-projects/{project_id}/skills",
        headers=owner_headers,
        json={
            "name": "程序设计教育科研规范测试 Skill",
            "description": "项目实验与写作规范。",
            "memory_ids": [memory.json()["id"]],
        },
    )
    assert skill.status_code == 201, skill.text
    assert skill.json()["enabled"] is False
    assert skill.json()["validation_status"] == "pending"

    manuscript = client.post(
        f"/api/research-projects/{project_id}/manuscripts",
        headers=owner_headers,
        json={"title": "错误类型自适应反馈研究", "content": "", "bibliography": ""},
    )
    assert manuscript.status_code == 201, manuscript.text
    manuscript_id = manuscript.json()["id"]
    base_version = manuscript.json()["version"]
    assert "\\begin{document}" in manuscript.json()["content"]

    saved = client.put(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}",
        headers=editor_headers,
        json={
            "content": manuscript.json()["content"].replace("请填写摘要。", "本文研究自适应反馈。"),
            "bibliography": "@article{test2025,title={Test}}",
            "base_version": base_version,
            "change_summary": "共同作者补充摘要",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2

    stale = client.put(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}",
        headers=owner_headers,
        json={
            "content": manuscript.json()["content"],
            "bibliography": "",
            "base_version": base_version,
            "change_summary": "基于旧版本保存",
        },
    )
    assert stale.status_code == 409

    comment = client.post(
        f"/api/research-projects/{project_id}/comments",
        headers=owner_headers,
        json={
            "manuscript_id": manuscript_id,
            "line_start": 7,
            "line_end": 8,
            "quote": "本文研究自适应反馈。",
            "content": "请在摘要中补充样本量与主要效应值。",
        },
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["line_start"] == 7

    preview = client.post(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}/preview",
        headers=editor_headers,
    )
    assert preview.status_code == 200
    assert preview.json()["title"] == "错误类型自适应反馈研究"

    restore = client.post(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}/restore",
        headers=owner_headers,
        json={"version": 1, "base_version": 2},
    )
    assert restore.status_code == 200, restore.text
    assert restore.json()["version"] == 3

    review = client.post(
        f"/api/research-projects/{project_id}/reviews",
        headers=owner_headers,
        json={"manuscript_id": manuscript_id, "roles": ["domain", "method", "writing"]},
    )
    assert review.status_code == 201, review.text
    assert review.json()["status"] == "completed"
    assert review.json()["items"]
    stream_events = []
    with client.stream(
        "POST",
        f"/api/research-projects/{project_id}/reviews/stream",
        headers=owner_headers,
        json={"manuscript_id": manuscript_id, "roles": ["domain", "writing"]},
    ) as streamed:
        assert streamed.status_code == 200
        for line in streamed.iter_lines():
            if line.startswith("data: "):
                import json

                stream_events.append(json.loads(line[6:]))
    assert any(event["type"] == "reviewer_started" for event in stream_events)
    assert any(event["type"] == "reviewer_completed" for event in stream_events)
    assert any(event["type"] == "review_result" for event in stream_events)
    review_item = review.json()["items"][0]
    response = client.patch(
        f"/api/research-projects/{project_id}/reviews/items/{review_item['id']}",
        headers=editor_headers,
        json={"status": "resolved", "response": "已补充随机种子、数据版本与统计检验。"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"

    presence = client.put(
        f"/api/research-projects/{project_id}/presence",
        headers=editor_headers,
        json={"page": "writing", "cursor": {"manuscript_id": manuscript_id, "line": 12}},
    )
    assert presence.status_code == 200
    online = client.get(
        f"/api/research-projects/{project_id}/presence", headers=owner_headers
    ).json()
    assert any(item["display_name"] == "共同作者" for item in online)

    compile_result = client.post(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}/compile",
        headers=owner_headers,
    )
    assert compile_result.status_code in {200, 409, 422}
    if compile_result.status_code == 409:
        assert "LaTeX 引擎" in compile_result.json()["detail"]


def test_latex_compiler_blocks_dangerous_commands(client):
    owner = register(client, "latex_security_owner", "LaTeX 安全测试")
    headers = auth(owner)
    project = client.post(
        "/api/research-projects",
        headers=headers,
        json={"name": "LaTeX 安全项目", "research_question": "验证编译沙箱"},
    ).json()
    manuscript = client.post(
        f"/api/research-projects/{project['id']}/manuscripts",
        headers=headers,
        json={
            "title": "恶意命令测试",
            "content": "\\documentclass{article}\\begin{document}\\input{C:/secret.txt}\\end{document}",
            "bibliography": "",
        },
    ).json()
    response = client.post(
        f"/api/research-projects/{project['id']}/manuscripts/{manuscript['id']}/compile",
        headers=headers,
    )
    assert response.status_code == 422
    assert "安全检查" in response.json()["detail"]
