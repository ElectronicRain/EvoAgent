from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import KnowledgeBase, Workflow, WorkflowRun
from .agents import agent_engine
from .common import audit, dumps, loads
from .knowledge import knowledge_service
from .tools import tool_runtime
from .security import runtime_security_service


TOKEN_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def resolve_path(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return current


def render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item, context) for item in value]
    if not isinstance(value, str):
        return value
    full = TOKEN_PATTERN.fullmatch(value)
    if full:
        return resolve_path(context, full.group(1))
    return TOKEN_PATTERN.sub(lambda match: str(resolve_path(context, match.group(1))), value)


class WorkflowEngine:
    def _order_nodes(self, definition: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = {item["id"]: item for item in definition.get("nodes", [])}
        edges = definition.get("edges", [])
        incoming = {node_id: set() for node_id in nodes}
        outgoing = {node_id: set() for node_id in nodes}
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source in nodes and target in nodes:
                incoming[target].add(source)
                outgoing[source].add(target)
        queue = [node_id for node_id, parents in incoming.items() if not parents]
        ordered = []
        while queue:
            node_id = queue.pop(0)
            ordered.append(nodes[node_id])
            for target in outgoing[node_id]:
                incoming[target].discard(node_id)
                if not incoming[target]:
                    queue.append(target)
        if len(ordered) != len(nodes):
            raise ValueError("工作流包含循环依赖")
        return ordered

    async def run(
        self,
        db: AsyncSession,
        workflow_id: str,
        workflow_input: dict[str, Any],
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> WorkflowRun:
        workflow = await db.get(Workflow, workflow_id)
        if not workflow or not workflow.enabled:
            raise LookupError("工作流不存在或未启用")
        run = WorkflowRun(
            workflow_id=workflow.id,
            status="running",
            input_json=dumps(workflow_input),
        )
        db.add(run)
        await db.flush()
        if on_event:
            await on_event({"type": "workflow_run_started", "run_id": run.id})
        started = time.perf_counter()
        trace: list[dict[str, Any]] = []
        context: dict[str, Any] = {"input": workflow_input, "nodes": {}}
        try:
            definition = loads(workflow.definition_json, {"nodes": [], "edges": []})
            for node in self._order_nodes(definition):
                node_id = node["id"]
                node_type = node.get("type", "agent")
                node_label = str(node.get("label") or node_id)
                config = render_value(node.get("config", {}), context)
                started_node = time.perf_counter()
                if on_event:
                    await on_event(
                        {
                            "type": "workflow_node_started",
                            "run_id": run.id,
                            "node_id": node_id,
                            "node_type": node_type,
                            "label": node_label,
                        }
                    )
                if node_type == "input":
                    result: Any = workflow_input
                elif node_type == "agent":
                    agent_run = await agent_engine.run(
                        db,
                        str(config["agent_id"]),
                        str(config.get("input", workflow_input.get("task", ""))),
                    )
                    result = {
                        "status": agent_run.status,
                        "output": agent_run.output_text,
                        "run_id": agent_run.id,
                        "error": agent_run.error,
                    }
                    if agent_run.status == "failed":
                        raise RuntimeError(
                            f"Agent 节点“{node_label}”执行失败："
                            f"{agent_run.error or '模型请求未返回错误详情'}"
                        )
                elif node_type == "knowledge":
                    knowledge_base_id = str(config.get("knowledge_base_id") or "")
                    knowledge_base = (
                        await db.get(KnowledgeBase, knowledge_base_id)
                        if knowledge_base_id
                        else None
                    )
                    if not knowledge_base:
                        raise LookupError(f"知识库节点“{node_label}”绑定的知识库不存在")
                    query = str(
                        config.get("query")
                        or config.get("input")
                        or workflow_input.get("task", "")
                    ).strip()
                    if not query:
                        raise ValueError(f"知识库节点“{node_label}”没有可检索的问题")
                    try:
                        top_k = max(1, min(int(config.get("top_k", 5)), 20))
                    except (TypeError, ValueError):
                        top_k = 5
                    chunks = await knowledge_service.search(
                        db,
                        query,
                        knowledge_base_ids=[knowledge_base.id],
                        top_k=top_k,
                    )
                    excerpts = []
                    for index, chunk in enumerate(chunks, 1):
                        title = str(chunk.get("title") or f"片段 {index}")
                        content = str(chunk.get("content") or "").strip()
                        citation = str(chunk.get("citation") or "").strip()
                        excerpt = f"[{index}] {title}\n{content}"
                        if citation:
                            excerpt += f"\n来源：{citation}"
                        excerpts.append(excerpt)
                    result = {
                        "status": "completed",
                        "knowledge_base_id": knowledge_base.id,
                        "knowledge_base_name": knowledge_base.name,
                        "query": query,
                        "chunk_count": len(chunks),
                        "chunks": chunks,
                        "output": (
                            "\n\n".join(excerpts)
                            if excerpts
                            else f"知识库“{knowledge_base.name}”没有检索到相关内容。"
                        ),
                    }
                elif node_type == "tool":
                    security = await runtime_security_service.resolve(
                        db, str(config.get("security_profile", "default"))
                    )
                    result = await tool_runtime.execute(
                        db,
                        str(config["tool"]),
                        dict(config.get("arguments", {})),
                        run_id=run.id,
                        permission_mode=str(config.get("permission_mode", "ask")),
                        security_context=security,
                    )
                elif node_type == "condition":
                    left = config.get("left")
                    operator = config.get("operator", "equals")
                    right = config.get("right")
                    passed = (
                        left == right
                        if operator == "equals"
                        else str(right) in str(left)
                        if operator == "contains"
                        else bool(left)
                    )
                    result = {"passed": passed, "left": left, "operator": operator, "right": right}
                    if not passed and config.get("stop_on_false", False):
                        context["nodes"][node_id] = result
                        break
                elif node_type == "output":
                    result = config.get("value", context["nodes"])
                else:
                    raise ValueError(f"不支持的节点类型: {node_type}")
                context["nodes"][node_id] = result
                completed_event = {
                    "node_id": node_id,
                    "type": node_type,
                    "status": "completed",
                    "duration_ms": int((time.perf_counter() - started_node) * 1000),
                }
                trace.append(completed_event)
                if on_event:
                    await on_event(
                        {
                            **completed_event,
                            "type": "workflow_node_completed",
                            "node_type": node_type,
                            "run_id": run.id,
                            "label": node_label,
                        }
                    )
            output_nodes = [
                node for node in definition.get("nodes", []) if node.get("type") == "output"
            ]
            output = (
                context["nodes"].get(output_nodes[-1]["id"], {})
                if output_nodes
                else context["nodes"]
            )
            run.status = "completed"
            run.output_json = dumps(output)
            run.trace_json = dumps(trace)
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await audit(db, "workflow.completed", "workflow_run", run.id)
            if on_event:
                await on_event(
                    {
                        "type": "workflow_run_completed",
                        "run_id": run.id,
                        "duration_ms": run.duration_ms,
                    }
                )
        except Exception as exc:
            error_message = str(exc).strip() or f"{type(exc).__name__}：执行未返回错误详情"
            run.status = "failed"
            run.error = error_message
            run.output_json = dumps(context["nodes"])
            run.trace_json = dumps(trace + [{"status": "failed", "error": error_message}])
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await audit(
                db,
                "workflow.failed",
                "workflow_run",
                run.id,
                {"error": error_message},
                success=False,
            )
            if on_event:
                await on_event(
                    {
                        "type": "workflow_run_failed",
                        "run_id": run.id,
                        "error": error_message,
                    }
                )
        await db.flush()
        return run


workflow_engine = WorkflowEngine()
