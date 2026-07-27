from __future__ import annotations

from backend.app.services.workflow_expert import WorkflowExpert


def test_review_and_revision_nodes_receive_the_manuscript_and_evidence():
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "label": "任务输入", "config": {}},
            {"id": "research", "type": "agent", "label": "文献检索 Agent", "config": {}},
            {"id": "body", "type": "agent", "label": "正文撰写 Agent", "config": {}},
            {"id": "review", "type": "agent", "label": "质量评审 Agent", "config": {}},
            {"id": "revise", "type": "agent", "label": "终稿修订 Agent", "config": {}},
            {"id": "output", "type": "output", "label": "输出", "config": {}},
        ],
        "edges": [
            {"source": "input", "target": "research"},
            {"source": "research", "target": "body"},
            {"source": "body", "target": "review"},
            {"source": "review", "target": "revise"},
            {"source": "revise", "target": "output"},
        ],
    }

    WorkflowExpert._preserve_review_context(definition)
    pairs = {(edge["source"], edge["target"]) for edge in definition["edges"]}
    nodes = {node["id"]: node for node in definition["nodes"]}

    assert ("research", "review") in pairs
    assert ("research", "revise") in pairs
    assert ("body", "revise") in pairs
    assert nodes["review"]["config"]["auto_input"] is True
    assert nodes["revise"]["config"]["input_context_char_limit"] == 100000
