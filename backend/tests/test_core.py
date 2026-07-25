from __future__ import annotations

import pytest

from backend.app.services.knowledge import chunk_text
from backend.app.services.intent import intent_service
from backend.app.services.secrets import secret_store
from backend.app.services.tools import ToolRuntime
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
