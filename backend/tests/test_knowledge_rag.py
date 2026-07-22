from __future__ import annotations

import io
import sqlite3
import uuid

import pytest
from pptx import Presentation

from backend.app.models import KnowledgeProviderConfig
from backend.app.services.knowledge_processing import (
    ExtractedSection,
    clean_text,
    extract_sections,
    hierarchical_chunks,
)
from backend.app.services.knowledge_vector import EmbeddingClient, RerankClient
from backend.app.services.secrets import secret_store


def _new_base(client):
    response = client.post(
        "/api/knowledge-bases",
        json={"name": f"测试知识库-{uuid.uuid4()}", "discipline": "计算机", "description": "RAG 测试"},
    )
    assert response.status_code == 201
    return response.json()


def test_cleaning_and_hierarchical_chunking():
    cleaned, stats = clean_text("页眉\n页眉\n页眉\n\n正文 A。\n正文 B。\n\n页眉")
    assert cleaned.count("页眉") == 1
    assert stats["repeated_lines_removed"] == 3
    drafts, chunk_stats = hierarchical_chunks(
        [ExtractedSection(("网格质量评价包括正交性、扭曲度和长宽比。 " * 80), "评价指标", "第 2 页")]
    )
    assert any(item.level == "parent" for item in drafts)
    assert any(item.level == "child" and item.parent_index is not None for item in drafts)
    assert all(item.metadata["locator"] == "第 2 页" for item in drafts)
    assert chunk_stats["duplicate_chunks_removed"] >= 0


def test_pptx_extraction():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "结构化网格"
    slide.placeholders[1].text = "正交性与雅可比行列式"
    buffer = io.BytesIO()
    presentation.save(buffer)
    sections, mime = extract_sections("lecture.pptx", buffer.getvalue())
    assert mime.endswith("presentationml.presentation")
    assert sections[0].metadata["slide"] == 1
    assert "雅可比" in sections[0].text


def test_provider_config_is_safe_and_has_defaults(client):
    response = client.get("/api/knowledge/config")
    assert response.status_code == 200
    data = response.json()
    assert data["embedding_model"] == "Qwen/Qwen3-VL-Embedding-8B"
    assert data["rerank_model"] == "BAAI/bge-reranker-v2-m3"
    assert "api_key_ciphertext" not in data


def test_full_text_ingestion_and_rag_query(client):
    base = _new_base(client)
    content = (
        "二维结构化网格质量通常通过正交性、长宽比、扭曲度和雅可比行列式评价。\n\n"
        "雅可比行列式为负意味着单元发生翻转，应当判定为无效网格。\n\n"
        "评价时还应结合目标数值求解器进行误差和稳定性验证。"
    )
    uploaded = client.post(
        f"/api/knowledge-bases/{base['id']}/documents/text",
        json={"title": "网格质量指南", "content": content, "source": "测试标准"},
    )
    assert uploaded.status_code == 201

    response = client.post(
        "/api/knowledge/query",
        json={
            "query": "怎样判断二维结构化网格是否发生单元翻转？",
            "knowledge_base_ids": [base["id"]],
            "top_k": 4,
            "generate_answer": True,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["chunks"]
    assert any("雅可比" in item["content"] for item in result["chunks"])
    assert result["citations"][0]["document_id"]
    assert result["trace"]["embedding_model"] == "Qwen/Qwen3-VL-Embedding-8B"
    assert result["answer"]


def test_duplicate_document_is_not_counted_twice(client):
    base = _new_base(client)
    payload = {"title": "重复资料", "content": "完全相同的有效知识内容。" * 20, "source": "测试"}
    first = client.post(f"/api/knowledge-bases/{base['id']}/documents/text", json=payload)
    second = client.post(f"/api/knowledge-bases/{base['id']}/documents/text", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    bases = client.get("/api/knowledge-bases").json()
    current = next(item for item in bases if item["id"] == base["id"])
    assert current["document_count"] == 1


def test_database_source_full_sync(client, tmp_path):
    path = tmp_path / "source.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE papers(title TEXT, abstract TEXT)")
    connection.execute(
        "INSERT INTO papers VALUES (?, ?)",
        ("Mesh quality", "Jacobian determinant and orthogonality are core metrics."),
    )
    connection.commit()
    connection.close()
    base = _new_base(client)
    response = client.post(
        f"/api/knowledge-bases/{base['id']}/sources/database",
        json={
            "name": "论文数据库",
            "connection_url": f"sqlite:///{path.as_posix()}",
            "query": "SELECT title, abstract FROM papers",
            "sync_now": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job"]["status"] == "completed"
    assert body["job"]["document_count"] == 1
    sources = client.get(f"/api/knowledge-bases/{base['id']}/sources").json()
    assert sources[0]["status"] == "ready"
    assert "config_ciphertext" not in sources[0]


def test_external_sources_can_be_registered_without_immediate_network(client):
    base = _new_base(client)
    web = client.post(
        f"/api/knowledge-bases/{base['id']}/sources/web",
        json={"name": "规范站点", "url": "https://example.com/standards", "sync_now": False},
    )
    api = client.post(
        f"/api/knowledge-bases/{base['id']}/sources/api",
        json={
            "name": "第三方数据",
            "url": "https://example.com/api/data",
            "headers": {"Authorization": "Bearer secret"},
            "response_path": "data.items",
            "sync_now": False,
        },
    )
    assert web.status_code == api.status_code == 201
    listed = client.get(f"/api/knowledge-bases/{base['id']}/sources").json()
    assert {item["source_type"] for item in listed} == {"web", "api"}
    assert all("config_ciphertext" not in item for item in listed)


@pytest.mark.asyncio
async def test_siliconflow_embedding_and_rerank_payloads(monkeypatch):
    calls = []

    class FakeResponse:
        is_error = False
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            if calls[-1][0].endswith("embeddings"):
                return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
            return {"results": [{"index": 1, "relevance_score": 0.98}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr("backend.app.services.knowledge_vector.httpx.AsyncClient", FakeClient)
    config = KnowledgeProviderConfig(
        id="mock",
        api_key_ciphertext=secret_store.encrypt("test-only-key"),
        embedding_base_url="https://mock.local/v1/embeddings",
        rerank_base_url="https://mock.local/v1/rerank",
    )
    vectors = await EmbeddingClient(config).embed(["网格质量"])
    ranks = await RerankClient(config).rerank("质量", ["无关", "网格质量"], 1)
    assert vectors == [[0.1, 0.2, 0.3]]
    assert ranks == [(1, 0.98)]
    assert calls[0][1]["json"]["model"] == "Qwen/Qwen3-VL-Embedding-8B"
    assert calls[1][1]["json"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert calls[1][1]["json"]["top_n"] == 1
