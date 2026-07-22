from __future__ import annotations

import pytest

from backend.app.services.knowledge import chunk_text
from backend.app.services.secrets import secret_store
from backend.app.services.workflows import WorkflowEngine, render_value


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


def test_knowledge_chunking_has_overlap_safe_output():
    content = "第一段。" * 200 + "\n\n" + "第二段。" * 200
    chunks = chunk_text(content, chunk_size=200, overlap=20)
    assert len(chunks) > 2
    assert all(len(item) <= 200 for item in chunks)


def test_secret_store_round_trip_and_ciphertext_is_not_plaintext():
    ciphertext = secret_store.encrypt("private-key")
    assert ciphertext != "private-key"
    assert secret_store.decrypt(ciphertext) == "private-key"
