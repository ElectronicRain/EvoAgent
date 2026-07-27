from __future__ import annotations

from backend.app.services.workflows import WorkflowEngine


def test_delivery_bindings_follow_the_actual_terminal_node():
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "config": {}},
            {"id": "old_outline", "type": "template", "config": {"template": "old"}},
            {"id": "final_reviser", "type": "template", "config": {"template": "final"}},
            {
                "id": "artifact",
                "type": "artifact",
                "config": {"content": "{{nodes.old_outline.output}}"},
            },
            {
                "id": "output",
                "type": "output",
                "config": {"value": {"result": "{{nodes.old_outline.output}}"}},
            },
        ],
        "edges": [
            {"source": "input", "target": "old_outline"},
            {"source": "old_outline", "target": "final_reviser"},
            {"source": "final_reviser", "target": "artifact"},
            {"source": "artifact", "target": "output"},
        ],
    }

    normalized = WorkflowEngine.normalized_definition(definition)
    nodes = {node["id"]: node for node in normalized["nodes"]}

    assert nodes["artifact"]["config"]["content"] == (
        "# 工作流产出\n\n{{nodes.final_reviser.output}}"
    )
    assert nodes["output"]["config"]["value"] == {
        "result": "{{nodes.artifact.output}}"
    }
    assert definition["nodes"][3]["config"]["content"] == "{{nodes.old_outline.output}}"


def test_non_artifact_output_schema_is_preserved():
    definition = {
        "nodes": [
            {"id": "input", "type": "input", "config": {}},
            {"id": "search", "type": "template", "config": {"template": "result"}},
            {
                "id": "output",
                "type": "output",
                "config": {
                    "value": {
                        "answer": "{{nodes.search.output}}",
                        "count": 3,
                    }
                },
            },
        ],
        "edges": [
            {"source": "input", "target": "search"},
            {"source": "search", "target": "output"},
        ],
    }

    normalized = WorkflowEngine.normalized_definition(definition)
    output = next(node for node in normalized["nodes"] if node["id"] == "output")
    assert output["config"]["value"] == {
        "answer": "{{nodes.search.output}}",
        "count": 3,
    }
