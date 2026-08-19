from __future__ import annotations

import io

from pypdf import PdfWriter


def register(client, username: str) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": username,
            "password": "teaching-pass-2026",
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    return result, {"Authorization": f"Bearer {result['token']}"}


def create_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_teaching_classroom_import_session_control_and_annotations(client):
    _user, auth = register(client, "teaching_learner")
    created = client.post(
        "/api/learning-projects",
        headers=auth,
        json={
            "name": "计算机网络课堂",
            "description": "学习可靠传输和拥塞控制。",
            "target": "能够解释 TCP 慢启动并分析拥塞窗口变化。",
            "current_level": "beginner",
            "target_level": "proficient",
            "weekly_hours": 5,
            "track": "计算机网络",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    uploaded = client.post(
        f"/api/learning-projects/{project_id}/teaching/documents",
        headers=auth,
        files={"file": ("network-course.pdf", create_pdf(), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["page_count"] == 2
    assert document["has_rendered_file"] is True

    session_response = client.post(
        f"/api/learning-projects/{project_id}/teaching/sessions",
        headers=auth,
        json={
            "document_id": document["id"],
            "pace": "standard",
            "depth": "course",
            "duration_minutes": 45,
            "proactive_questions": True,
        },
    )
    assert session_response.status_code == 201, session_response.text
    session = session_response.json()
    assert session["status"] == "ready"
    assert session["document"]["id"] == document["id"]
    assert len(session["lesson_plan"]) == 2
    assert session["turns"][0]["metadata"]["kind"] == "greeting"

    started = client.patch(
        f"/api/learning-projects/{project_id}/teaching/sessions/{session['id']}/control",
        headers=auth,
        json={"action": "start", "page": 1},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "explaining"

    turn = client.post(
        f"/api/learning-projects/{project_id}/teaching/sessions/{session['id']}/turns",
        headers=auth,
        json={"message": "慢启动为什么近似指数增长？", "action": "ask", "page": 1},
    )
    assert turn.status_code == 201, turn.text
    answer = turn.json()
    assert answer["role"] == "assistant"
    assert answer["page"] == 1
    assert len(answer["content"]) <= 181
    assert answer["metadata"]["micro_turn"] is True

    saved = client.put(
        f"/api/learning-projects/{project_id}/teaching/sessions/{session['id']}/annotations",
        headers=auth,
        json={
            "annotations": [
                {
                    "page": 1,
                    "author": "student",
                    "kind": "pen",
                    "payload": {"points": [{"x": 0.1, "y": 0.2}, {"x": 0.2, "y": 0.3}]},
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] == 1

    restored = client.get(
        f"/api/learning-projects/{project_id}/teaching/sessions/{session['id']}",
        headers=auth,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["annotations"][0]["payload"]["points"][1]["x"] == 0.2

    downloaded = client.get(
        f"/api/learning-projects/{project_id}/teaching/documents/{document['id']}/file",
        headers=auth,
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/pdf")


def test_standalone_teaching_studio_has_no_learning_direction_dependency(client):
    _user, auth = register(client, "standalone_teacher_user")

    before = client.get("/api/learning-projects", headers=auth)
    assert before.status_code == 200, before.text
    assert before.json() == []

    uploaded = client.post(
        "/api/teaching/documents",
        headers=auth,
        files={"file": ("standalone-network.pdf", create_pdf(), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    assert document["page_count"] == 2
    assert document["metadata"]["standalone"] is True
    assert "project_id" not in document

    created = client.post(
        "/api/teaching/sessions",
        headers=auth,
        json={
            "document_id": document["id"],
            "pace": "standard",
            "depth": "course",
            "duration_minutes": 45,
            "proactive_questions": True,
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert "project_id" not in session
    assert session["document_id"] == document["id"]
    assert session["turns"][0]["metadata"]["standalone"] is True

    explained = client.post(
        f"/api/teaching/sessions/{session['id']}/turns",
        headers=auth,
        json={"message": "解释当前页", "action": "ask", "page": 2},
    )
    assert explained.status_code == 201, explained.text
    turn = explained.json()
    assert turn["page"] == 2
    assert turn["metadata"]["standalone"] is True
    assert turn["metadata"]["board_action_count"] >= 1
    assert not turn["commands"] or turn["commands"][0]["type"] == "focus_text"
    assert any(
        command["type"] in {"highlight_text", "circle_text", "write_note", "write_formula"}
        for command in turn["commands"]
    )
    assert turn["citations"][0]["source"].startswith("本地课件：")

    saved = client.put(
        f"/api/teaching/sessions/{session['id']}/annotations",
        headers=auth,
        json={
            "annotations": [
                {
                    "page": 2,
                    "author": "student",
                    "kind": "highlighter",
                    "payload": {"points": [{"x": 0.1, "y": 0.1}]},
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    restored = client.get(f"/api/teaching/sessions/{session['id']}", headers=auth)
    assert restored.status_code == 200, restored.text
    assert restored.json()["annotations"][0]["page"] == 2

    downloaded = client.get(
        f"/api/teaching/documents/{document['id']}/file", headers=auth
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/pdf")

    after = client.get("/api/learning-projects", headers=auth)
    assert after.status_code == 200, after.text
    assert after.json() == []
