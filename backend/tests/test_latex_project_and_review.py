from __future__ import annotations

import io
import zipfile


def register(client, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": username,
            "password": "research-pass-2026",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(result: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {result['token']}"}


def latex_zip(*, unsafe: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if unsafe:
            archive.writestr("../outside.tex", "unsafe")
        else:
            archive.writestr(
                "paper/main.tex",
                r"""\documentclass{article}
\title{Imported Multi-file Paper}
\begin{document}
\maketitle
\begin{abstract}A reproducible computer science study.\end{abstract}
\input{sections/method}
\bibliography{references}
\end{document}
""",
            )
            archive.writestr(
                "paper/sections/method.tex",
                r"""\section{Method}
We compare against a baseline using random seed = 42 and sample size n=120.
\section{Experiment}
We report effect size, confidence interval and p < 0.05.\cite{test2026}
\section{Limitations}
We discuss external validity and failure cases.
""",
            )
            archive.writestr(
                "paper/references.bib", "@article{test2026,title={Test},year={2026}}"
            )
            archive.writestr("paper/figures/result.png", b"\x89PNG\r\n\x1a\n")
    return buffer.getvalue()


def test_import_multifile_edit_diff_export_and_review(client, monkeypatch):
    from backend.app.services import research_projects as research_module

    async def no_online_endpoint(_db):
        return None

    monkeypatch.setattr(research_module, "latest_chat_endpoint", no_online_endpoint)
    owner = register(client, "latex_import_owner")
    headers = auth(owner)
    project = client.post(
        "/api/research-projects",
        headers=headers,
        json={
            "name": "LaTeX import project",
            "discipline": "Computer Science",
            "research_question": "Can the imported multi-file workflow be reproduced?",
        },
    ).json()
    project_id = project["id"]

    imported = client.post(
        f"/api/research-projects/{project_id}/manuscripts/import",
        headers=headers,
        files=[("files", ("paper.zip", latex_zip(), "application/zip"))],
        data={"title": "", "main_file": "", "paths_json": "[]"},
    )
    assert imported.status_code == 201, imported.text
    manuscript = imported.json()
    manuscript_id = manuscript["id"]
    assert manuscript["main_file"] == "main.tex"
    assert set(manuscript["files"]) == {
        "main.tex",
        "sections/method.tex",
        "references.bib",
        "figures/result.png",
    }
    assert manuscript["files"]["figures/result.png"]["encoding"] == "base64"
    assert any(section["title"] == "Method" for section in manuscript["preview"]["sections"])

    files = manuscript["files"]
    files["sections/method.tex"]["content"] += "\nAdditional ablation experiment.\n"
    saved = client.put(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}",
        headers=headers,
        json={
            "content": files["main.tex"]["content"],
            "bibliography": files["references.bib"]["content"],
            "main_file": "main.tex",
            "files": files,
            "base_version": 1,
            "change_summary": "Add ablation experiment",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2

    diff = client.get(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}/versions/1/diff",
        headers=headers,
        params={"file_path": "sections/method.tex"},
    )
    assert diff.status_code == 200, diff.text
    assert "+Additional ablation experiment." in diff.json()["diff"]

    exported = client.post(
        f"/api/research-projects/{project_id}/manuscripts/{manuscript_id}/export",
        headers=headers,
        json={},
    )
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "sections/method.tex" in archive.namelist()
        assert b"Additional ablation experiment" in archive.read("sections/method.tex")

    comment = client.post(
        f"/api/research-projects/{project_id}/comments",
        headers=headers,
        json={
            "manuscript_id": manuscript_id,
            "file_path": "sections/method.tex",
            "line_start": 1,
            "line_end": 2,
            "content": "Clarify this method section.",
        },
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["anchored_version"] == 2
    assert "Method" in comment.json()["quote"]

    review = client.post(
        f"/api/research-projects/{project_id}/reviews",
        headers=headers,
        json={
            "manuscript_id": manuscript_id,
            "roles": ["domain", "method", "experiment", "statistics", "strict"],
            "venue": "Top Computer Science Conference",
            "rigor": "top_venue",
            "focus": "reproducibility and statistics",
        },
    )
    assert review.status_code == 201, review.text
    body = review.json()
    assert len(body["report"]["reviewer_reports"]) == 5
    assert set(body["scores"]) == {
        "novelty",
        "correctness",
        "reproducibility",
        "significance",
        "clarity",
    }
    assert body["report"]["committee"]["decision_threshold"] == 78
    assert all(0 <= score <= 100 for score in body["scores"].values())
    assert body["items"]
    assert all(item["reviewer_role"] in {"domain", "method", "experiment", "statistics", "strict"} for item in body["items"])
    assert all(0 <= item["confidence"] <= 1 for item in body["items"])


def test_zip_path_traversal_is_rejected(client):
    owner = register(client, "latex_zip_safety")
    headers = auth(owner)
    project = client.post(
        "/api/research-projects",
        headers=headers,
        json={"name": "ZIP safety", "research_question": "Block traversal"},
    ).json()
    response = client.post(
        f"/api/research-projects/{project['id']}/manuscripts/import",
        headers=headers,
        files=[("files", ("unsafe.zip", latex_zip(unsafe=True), "application/zip"))],
        data={"paths_json": "[]"},
    )
    assert response.status_code == 422
    assert "不安全路径" in response.json()["detail"]


def test_multifile_upload_accepts_image_and_bib_relative_paths(client):
    owner = register(client, "latex_relative_assets")
    headers = auth(owner)
    project = client.post(
        "/api/research-projects",
        headers=headers,
        json={"name": "Relative assets", "research_question": "Render assets"},
    ).json()
    response = client.post(
        f"/api/research-projects/{project['id']}/manuscripts/import",
        headers=headers,
        files=[
            ("files", ("main.tex", b"\\documentclass{article}\\begin{document}\\includegraphics{figures/chart.png}\\bibliography{refs/library}\\end{document}", "text/x-tex")),
            ("files", ("chart.png", b"\x89PNG\r\n\x1a\n", "image/png")),
            ("files", ("library.bib", b"@article{x,title={X}}", "text/x-bibtex")),
        ],
        data={"paths_json": '["main.tex","figures/chart.png","refs/library.bib"]'},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["files"]["figures/chart.png"]["encoding"] == "base64"
    assert body["files"]["refs/library.bib"]["encoding"] == "utf8"
