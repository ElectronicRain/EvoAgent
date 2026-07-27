from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    AgentDefinition,
    KnowledgeBase,
    ModelEndpoint,
    Workflow,
    WorkflowArtifact,
    WorkflowRun,
)
from .agents import ExecutionContext, agent_engine
from .common import audit, dumps, loads
from .document_exports import output_to_markdown
from .knowledge import knowledge_service
from .llm import provider_from_endpoint
from .security import runtime_security_service
from .tools import tool_runtime


TOKEN_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
ACADEMIC_REVIEW_PATTERN = re.compile(
    r"综述|文献回顾|系统评价|系统综述|literature\s+review|systematic\s+review|review\s+article",
    re.I,
)


def resolve_path(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return ""
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


def compact_runtime_value(value: Any, depth: int = 0) -> Any:
    """Keep SSE progress useful without streaming full prompts, documents, or base64 images."""
    if depth >= 4:
        return "…"
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return "[图片数据已省略]"
        return value if len(value) <= 1600 else f"{value[:1600]}…"
    if isinstance(value, dict):
        return {
            str(key): compact_runtime_value(item, depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, list):
        return [compact_runtime_value(item, depth + 1) for item in value[:12]]
    if isinstance(value, tuple):
        return [compact_runtime_value(item, depth + 1) for item in value[:12]]
    return value


def intent_prompt_value(
    value: Any,
    depth: int = 0,
    *,
    string_limit: int = 12000,
) -> Any:
    """Keep enough evidence for final alignment without sending unbounded runtime data."""
    if depth >= 5:
        return "…"
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return "[图片数据已省略]"
        return value if len(value) <= string_limit else f"{value[:string_limit]}…"
    if isinstance(value, dict):
        return {
            str(key): intent_prompt_value(
                item,
                depth + 1,
                string_limit=string_limit,
            )
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [
            intent_prompt_value(item, depth + 1, string_limit=string_limit) for item in value[:16]
        ]
    return value


def primary_output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("result", "output", "content", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null", "no"}
    return bool(value)


def _condition(left: Any, operator: str, right: Any) -> bool:
    if operator == "equals":
        return left == right
    if operator == "not_equals":
        return left != right
    if operator == "contains":
        return str(right) in str(left)
    if operator == "not_contains":
        return str(right) not in str(left)
    if operator == "starts_with":
        return str(left).startswith(str(right))
    if operator == "ends_with":
        return str(left).endswith(str(right))
    if operator == "greater":
        try:
            return float(left) > float(right)
        except (TypeError, ValueError):
            return False
    if operator == "less":
        try:
            return float(left) < float(right)
        except (TypeError, ValueError):
            return False
    if operator == "exists":
        return left is not None and left != ""
    if operator == "empty":
        return left is None or left == "" or left == [] or left == {}
    if operator == "regex":
        try:
            return re.search(str(right), str(left)) is not None
        except re.error:
            return False
    return _truthy(left)


def _condition_expression(expression: str) -> bool:
    """Evaluate the small legacy expression format without executing user code."""
    expression = expression.strip()
    match = re.fullmatch(r"(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*?)", expression, re.S)
    if not match:
        return _truthy(expression)

    def literal(raw: str) -> Any:
        raw = raw.strip()
        lowered = raw.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"none", "null"}:
            return None
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            return raw[1:-1]
        try:
            return float(raw)
        except ValueError:
            return raw

    left = literal(match.group(1))
    right = literal(match.group(3))
    operator = match.group(2)
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    try:
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        if operator == "<=":
            return left <= right
    except TypeError:
        return False
    return False


def _safe_function(name: str, arguments: Any) -> Any:
    values = arguments if isinstance(arguments, list) else [arguments]
    if name == "concat":
        return "".join(str(item) for item in values)
    if name == "join":
        items = values[0] if values else []
        separator = str(values[1]) if len(values) > 1 else "\n"
        return separator.join(str(item) for item in (items if isinstance(items, list) else [items]))
    if name == "split":
        return str(values[0] if values else "").split(str(values[1] if len(values) > 1 else ","))
    if name == "length":
        try:
            return len(values[0])
        except (IndexError, TypeError):
            return 0
    if name == "unique":
        items = values[0] if values and isinstance(values[0], list) else values
        return list(dict.fromkeys(str(item) for item in items))
    if name == "json_parse":
        try:
            return json.loads(str(values[0] if values else "{}"))
        except json.JSONDecodeError:
            return {}
    if name == "json_stringify":
        return dumps(values[0] if values else {})
    if name == "pick":
        source = values[0] if values and isinstance(values[0], dict) else {}
        return source.get(str(values[1]), "") if len(values) > 1 else ""
    if name == "coalesce":
        return next((item for item in values if item not in (None, "", [], {})), "")
    raise ValueError(f"不支持的安全函数：{name}")


class WorkflowInterrupted(RuntimeError):
    pass


@dataclass
class WorkflowControl:
    run_id: str
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted: bool = False
    guidance: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    event_sequence: int = 0

    def __post_init__(self) -> None:
        self.gate.set()

    async def emit(self, event: dict[str, Any]) -> None:
        self.event_sequence += 1
        payload = {
            **event,
            "run_id": self.run_id,
            "event_id": self.event_sequence,
        }
        self.events = [*self.events, payload][-600:]
        if self.on_event:
            await self.on_event(payload)


class WorkflowEngine:
    def __init__(self) -> None:
        self.controls: dict[str, WorkflowControl] = {}

    @staticmethod
    def agent_node_policy_preset(label: str, config: dict[str, Any] | None = None) -> str:
        configured = str((config or {}).get("tool_policy") or "auto").strip().lower()
        if configured in {"planning", "research", "writing", "review", "balanced", "full"}:
            return configured
        profile = " ".join(
            [
                label,
                str((config or {}).get("prompt") or ""),
            ]
        ).lower()
        if re.search(r"规划|提纲|需求|拆解|计划|编排|plan|outline|requirement", profile):
            return "planning"
        if re.search(r"检索|搜索|文献|调研|资料搜集|research|search|literature", profile):
            return "research"
        if re.search(r"审核|评审|核验|校验|复核|修订|review|verify|revision", profile):
            return "review"
        if re.search(r"撰写|写作|成稿|综合生成|draft|write|synthesis", profile):
            return "writing"
        return "balanced"

    @classmethod
    def agent_node_tool_policy(
        cls, label: str, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = config or {}
        policy: dict[str, Any] = {"preset": cls.agent_node_policy_preset(label, config)}
        overrides = {
            "max_tool_iterations": "max_iterations",
            "max_tool_calls": "max_calls",
            "tool_result_char_limit": "result_char_limit",
            "tool_context_char_limit": "context_char_limit",
        }
        for source, target in overrides.items():
            if config.get(source) not in (None, ""):
                policy[target] = config[source]
        if isinstance(config.get("tool_allowlist"), list):
            policy["allowed_tools"] = config["tool_allowlist"]
        return policy

    @classmethod
    def agent_node_rag_policy(
        cls, label: str, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = config or {}
        configured = str(config.get("rag_mode") or "auto").strip().lower()
        preset = cls.agent_node_policy_preset(label, config)
        if configured not in {"agent", "off"}:
            configured = "off" if preset in {"research", "writing", "review"} else "agent"
        return {
            "mode": configured,
            # Workflow tasks have already passed clarification and contain explicit
            # node context, so an extra LLM query-rewrite call is normally redundant.
            "query_rewrite": bool(config.get("rag_query_rewrite", False)),
            "cross_language": bool(config.get("rag_cross_language", False)),
        }

    @classmethod
    def default_agent_node_prompt(cls, label: str) -> str:
        preset = cls.agent_node_policy_preset(label)
        if preset == "review" and re.search(r"修订|改写|润色|revision|revise", label, re.I):
            return (
                "依据待修订正文和评审意见逐项修复问题，保持已核验事实、引用和参考文献对应关系。"
                "只输出完整修订稿，不输出 DECISION 标记、修改过程或省略号；不得虚构文献、数据、DOI 或 URL。"
            )
        prompts = {
            "planning": (
                "只负责澄清目标并形成可执行提纲。直接依据用户已确认需求和系统附加的知识上下文，"
                "输出研究范围、核心问题、章节结构、证据需求与验收标准；不要读取本地文件、执行命令或重复检索。"
            ),
            "research": (
                "只负责取得并整理真实、可追溯的资料。优先提供题名、作者、年份、期刊、DOI 或 URL，"
                "区分已核验事实与待核验线索；达到用户要求的资料范围后立即停止检索并输出结构化证据表。"
            ),
            "writing": (
                "只依据用户目标和上游证据完成正式成稿。保持论点、证据和引用一一对应，"
                "不得虚构文献、实验数据或 DOI；使用可直接渲染的 Markdown，完整交付正文。"
            ),
            "review": (
                "对上游成果进行质量审核或修订，检查是否符合用户目标、证据是否可追溯、结构是否完整。"
                "审核节点首行输出 DECISION: PASS 或 DECISION: REVISE，并给出可执行修改项；修订节点输出完整修订稿。"
            ),
            "balanced": (
                "围绕当前节点职责完成可供下游直接使用的结构化结果。已有信息足够时直接输出；"
                "只有缺少任务必需的真实信息时才调用工具，并且不得重复相同查询。"
            ),
        }
        return prompts[preset]

    @staticmethod
    def prompt_looks_corrupted(value: str) -> bool:
        compact = re.sub(r"\s+", "", value or "")
        return (
            len(compact) >= 20
            and compact.count("?") >= 10
            and (compact.count("?") / len(compact)) >= 0.3
        )

    @staticmethod
    def _bounded_node_text(value: str, limit: int) -> tuple[str, int]:
        if len(value) <= limit:
            return value, 0
        marker = f"\n\n…[节点上下文已压缩 {len(value) - limit:,} 个字符]…\n\n"
        available = max(200, limit - len(marker))
        head = int(available * 0.7)
        compacted = f"{value[:head]}{marker}{value[-(available - head) :]}"
        return compacted, len(value) - len(compacted)

    @classmethod
    def agent_node_context_limit(cls, label: str, config: dict[str, Any]) -> int:
        defaults = {
            "planning": 16000,
            "research": 24000,
            "writing": 60000,
            "review": 60000,
            "balanced": 32000,
            "full": 80000,
        }
        preset = cls.agent_node_policy_preset(label, config)
        raw = config.get("input_context_char_limit", defaults[preset])
        try:
            return max(8000, min(int(raw), 120000))
        except (TypeError, ValueError):
            return defaults[preset]

    def events(self, run_id: str, after: int = 0) -> dict[str, Any]:
        control = self.controls.get(run_id)
        if not control:
            return {"run_id": run_id, "active": False, "events": []}
        return {
            "run_id": run_id,
            "active": True,
            "events": [event for event in control.events if int(event.get("event_id", 0)) > after],
            "latest_event_id": control.event_sequence,
        }

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
            raise ValueError("工作流包含结构循环；请使用运行设置中的受控循环")
        return ordered

    def validate_definition(self, definition: dict[str, Any]) -> None:
        nodes = list(definition.get("nodes") or [])
        edges = list(definition.get("edges") or [])
        if not nodes:
            raise ValueError("工作流至少需要任务输入和结果输出节点")
        ids = [str(item.get("id") or "").strip() for item in nodes]
        if any(not node_id for node_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("工作流节点 ID 不能为空或重复")
        node_map = dict(zip(ids, nodes, strict=True))
        inputs = [node_id for node_id, node in node_map.items() if node.get("type") == "input"]
        outputs = [node_id for node_id, node in node_map.items() if node.get("type") == "output"]
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("工作流必须且只能包含一个任务输入节点和一个结果输出节点")
        outgoing: dict[str, set[str]] = {node_id: set() for node_id in ids}
        incoming: dict[str, set[str]] = {node_id: set() for node_id in ids}
        condition_slots: dict[str, set[str]] = {}
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source not in node_map or target not in node_map:
                raise ValueError(f"工作流连线引用了不存在的节点：{source} → {target}")
            if source == target:
                raise ValueError(f"节点“{source}”不能连接到自身")
            outgoing[source].add(target)
            incoming[target].add(source)
            if node_map[source].get("type") == "condition":
                condition_slots.setdefault(source, set()).add(
                    str(
                        edge.get("source_slot")
                        or edge.get("sourceHandle")
                        or edge.get("branch")
                        or "output"
                    )
                )
        input_id, output_id = inputs[0], outputs[0]
        if incoming[input_id]:
            raise ValueError("任务输入节点不能有入线")
        if outgoing[output_id]:
            raise ValueError("结果输出节点不能有出线")
        reachable = {input_id}
        queue = [input_id]
        while queue:
            current = queue.pop(0)
            for target in outgoing[current]:
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        missing = [
            node_map[node_id].get("label") or node_id for node_id in ids if node_id not in reachable
        ]
        if missing:
            raise ValueError(f"以下节点无法从任务输入到达：{'、'.join(map(str, missing))}")
        can_reach_output = {output_id}
        queue = [output_id]
        while queue:
            current = queue.pop(0)
            for source in incoming[current]:
                if source not in can_reach_output:
                    can_reach_output.add(source)
                    queue.append(source)
        dead_ends = [
            node_map[node_id].get("label") or node_id
            for node_id in ids
            if node_id not in can_reach_output
        ]
        if dead_ends:
            raise ValueError(f"以下节点没有通向结果输出：{'、'.join(map(str, dead_ends))}")
        for node_id, node in node_map.items():
            if node.get("type") == "condition" and not {"true", "false"}.issubset(
                condition_slots.get(node_id, set())
            ):
                raise ValueError(
                    f"条件节点“{node.get('label') or node_id}”必须同时连接 TRUE 和 FALSE 分支"
                )
        self._order_nodes(definition)

    async def validate_runtime_definition(
        self,
        db: AsyncSession,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate that every referenced runtime resource can actually execute.

        Structural validation alone allowed an expert proposal to look correct on
        the canvas while still referring to a deleted Agent, disabled model
        endpoint, missing knowledge base, or unknown tool.  This gate is shared by
        save, expert materialization, and run so all three paths make the same
        promise to the UI.
        """
        self.validate_definition(definition)
        nodes = list(definition.get("nodes") or [])
        issues: list[str] = []

        agent_nodes = [item for item in nodes if item.get("type") == "agent"]
        agent_ids = {
            str((item.get("config") or {}).get("agent_id") or "")
            for item in agent_nodes
        }
        agent_ids.discard("")
        agents = {
            item.id: item
            for item in (
                await db.scalars(select(AgentDefinition).where(AgentDefinition.id.in_(agent_ids)))
                if agent_ids
                else []
            )
        }
        endpoint_ids = {
            str(item.model_endpoint_id)
            for item in agents.values()
            if item.model_endpoint_id
        }
        endpoints = {
            item.id: item
            for item in (
                await db.scalars(select(ModelEndpoint).where(ModelEndpoint.id.in_(endpoint_ids)))
                if endpoint_ids
                else []
            )
        }
        for node in agent_nodes:
            label = str(node.get("label") or node.get("id") or "Agent")
            agent_id = str((node.get("config") or {}).get("agent_id") or "")
            agent = agents.get(agent_id)
            if not agent:
                issues.append(f"Agent 节点“{label}”未绑定存在的 Agent")
                continue
            if agent.status not in {"active", "candidate"}:
                issues.append(f"Agent 节点“{label}”绑定的 Agent 当前为 {agent.status} 状态")
            if settings.require_online_agents:
                endpoint = endpoints.get(str(agent.model_endpoint_id or ""))
                if not endpoint or not endpoint.enabled or endpoint.modality != "chat":
                    issues.append(f"Agent 节点“{label}”未绑定启用的在线对话模型接口")

        knowledge_nodes = [item for item in nodes if item.get("type") == "knowledge"]
        knowledge_ids = {
            str((item.get("config") or {}).get("knowledge_base_id") or "")
            for item in knowledge_nodes
        }
        knowledge_ids.discard("")
        existing_knowledge = set(
            await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(knowledge_ids)))
        ) if knowledge_ids else set()
        for node in knowledge_nodes:
            label = str(node.get("label") or node.get("id") or "知识库")
            knowledge_id = str((node.get("config") or {}).get("knowledge_base_id") or "")
            if knowledge_id not in existing_knowledge:
                issues.append(f"知识库节点“{label}”绑定的知识库不存在")

        registered_tools = {
            str(item.get("function", {}).get("name") or "") for item in tool_runtime.schemas()
        }
        for node in nodes:
            if node.get("type") != "tool":
                continue
            label = str(node.get("label") or node.get("id") or "工具")
            tool_name = str((node.get("config") or {}).get("tool") or "")
            if not tool_name or tool_name not in registered_tools:
                issues.append(f"工具节点“{label}”未选择已注册的可用工具")

        node_ids = {str(item.get("id") or "") for item in nodes}
        variable_names = {
            str(item.get("name") or "") for item in definition.get("variables", [])
        }

        def inspect_tokens(value: Any, label: str) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    inspect_tokens(item, label)
                return
            if isinstance(value, list):
                for item in value:
                    inspect_tokens(item, label)
                return
            if not isinstance(value, str):
                return
            for match in TOKEN_PATTERN.finditer(value):
                path = match.group(1).strip()
                parts = path.split(".")
                if len(parts) >= 2 and parts[0] == "nodes" and parts[1] not in node_ids:
                    issues.append(f"节点“{label}”引用了不存在的节点变量：{path}")
                if len(parts) >= 2 and parts[0] == "variables" and parts[1] not in variable_names:
                    issues.append(f"节点“{label}”引用了未定义的工作流变量：{path}")

        for node in nodes:
            inspect_tokens(node.get("config") or {}, str(node.get("label") or node.get("id")))
        if issues:
            raise ValueError("工作流尚不可执行：" + "；".join(dict.fromkeys(issues)))
        return {
            "executable": True,
            "node_count": len(nodes),
            "edge_count": len(definition.get("edges") or []),
            "agent_count": len(agent_nodes),
            "knowledge_count": len(knowledge_nodes),
            "tool_count": sum(1 for item in nodes if item.get("type") == "tool"),
            "online_required": settings.require_online_agents,
        }

    @staticmethod
    def _retryable_node_error(exc: Exception) -> bool:
        if isinstance(exc, (LookupError, ValueError, KeyError)):
            return False
        message = str(exc).lower()
        if re.search(r"已(?:重试|尝试)|避免重复计费", message):
            return False
        if re.search(r"http\s+(400|401|402|403|404|422)\b", message):
            return False
        return bool(
            re.search(
                r"timeout|timed out|readtimeout|connect|temporar|"
                r"http\s+(408|409|425|429|500|502|503|504)\b",
                message,
            )
        )

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
        candidate = (
            fenced.group(1) if fenced else content[content.find("{") : content.rfind("}") + 1]
        )
        if not candidate:
            return None
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _requested_reference_count(task: str) -> int | None:
        for pattern in (
            r"(?:至少|不少于|约|大约|检索|搜寻|纳入|包含|覆盖)?\s*(\d{1,3})\s*(?:篇|条)\s*(?:文献|论文|资料)?",
            r"(\d{1,3})\s*(?:papers?|articles?|references?|studies)",
        ):
            match = re.search(pattern, task, re.I)
            if match:
                return max(1, min(int(match.group(1)), 500))
        return None

    @staticmethod
    def _research_source_count(context: dict[str, Any]) -> int:
        counts: list[int] = []
        for result in context.get("nodes", {}).values():
            if not isinstance(result, dict):
                continue
            research = result.get("research")
            if isinstance(research, dict):
                try:
                    counts.append(int(research.get("source_count") or 0))
                except (TypeError, ValueError):
                    pass
        return max(counts, default=0)

    @classmethod
    def _delivery_quality_issues(
        cls,
        workflow_input: dict[str, Any],
        output: Any,
        context: dict[str, Any],
    ) -> list[str]:
        task = str(workflow_input.get("task") or workflow_input)
        if not ACADEMIC_REVIEW_PATTERN.search(task):
            return []
        text = primary_output_text(output).strip()
        requested = cls._requested_reference_count(task)
        minimum_length = max(4500, min(12000, (requested or 12) * 220))
        issues: list[str] = []
        if len(text) < minimum_length:
            issues.append(
                f"综述正文仅 {len(text)} 字符，低于本任务完整交付的最低篇幅 {minimum_length} 字符"
            )
        required_sections = {
            "摘要/Abstract": (
                r"(?:^|\n)\s*(?:#{1,3}\s*(?:\d+(?:\.\d+)*[.)]?\s*)?|\*\*\s*)"
                r"(?:摘要|abstract)\b"
            ),
            "引言/Introduction": (
                r"(?:^|\n)\s*(?:#{1,3}\s*(?:\d+(?:\.\d+)*[.)]?\s*)?|\*\*\s*)"
                r"(?:引言|绪论|introduction)\b"
            ),
            "结论/Conclusion": (
                r"(?:^|\n)\s*(?:#{1,3}\s*(?:\d+(?:\.\d+)*[.)]?\s*)?|\*\*\s*)"
                r"(?:结论|总结与展望|conclusions?|discussion\s+and\s+conclusion)\b"
            ),
            "参考文献/References": (
                r"(?:^|\n)\s*(?:#{1,3}\s*(?:\d+(?:\.\d+)*[.)]?\s*)?|\*\*\s*)"
                r"(?:参考文献|references|bibliography)\b"
            ),
        }
        for label, pattern in required_sections.items():
            if not re.search(pattern, text, re.I):
                issues.append(f"缺少完整的“{label}”章节")
        reference_match = re.search(
            r"(?:^|\n)\s*(?:#{1,3}\s*(?:\d+(?:\.\d+)*[.)]?\s*)?|\*\*\s*)"
            r"(?:参考文献|references|bibliography)\b(?:\*\*)?([\s\S]*)$",
            text,
            re.I,
        )
        reference_block = reference_match.group(1) if reference_match else ""
        doi_refs = set(re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", reference_block, re.I))
        linked_refs = set(re.findall(r"https?://[^\s)>]+", reference_block, re.I))
        numbered_refs = re.findall(r"(?m)^\s*(?:\[\d+\]|\d+[.)])\s+\S", reference_block)
        reference_count = max(len(doi_refs), len(linked_refs), len(numbered_refs))
        if requested and reference_count < requested:
            issues.append(
                f"用户要求纳入 {requested} 篇文献，但参考文献列表仅识别到 {reference_count} 条"
            )
        source_count = cls._research_source_count(context)
        if requested and source_count < requested:
            issues.append(
                f"真实联网检索仅记录 {source_count} 条可追溯来源，未达到用户要求的 {requested} 篇"
            )
        elif source_count == 0:
            issues.append("没有记录任何真实联网文献来源，不能作为文献综述最终稿交付")
        tail = text[-180:].strip()
        if (
            re.search(r"(?:\\[A-Za-z]{0,8}|[,;:，；：\-–—])$", tail)
            or text.count("$$") % 2
            or text.count("\\begin{") != text.count("\\end{")
        ):
            issues.append("正文结尾或公式结构不完整，疑似被模型输出上限截断")
        return list(dict.fromkeys(issues))[:12]

    async def _align_result_with_intent(
        self,
        db: AsyncSession,
        workflow: Workflow,
        workflow_input: dict[str, Any],
        final_output: Any,
        context: dict[str, Any],
        control: WorkflowControl,
    ) -> tuple[Any, dict[str, Any]]:
        deterministic_issues = self._delivery_quality_issues(
            workflow_input,
            final_output,
            context,
        )
        task = str(workflow_input.get("task") or workflow_input)
        if deterministic_issues or ACADEMIC_REVIEW_PATTERN.search(task):
            passed = not deterministic_issues
            validation = {
                "passed": passed,
                "score": 100 if passed else 0,
                "issues": deterministic_issues,
                "improved": False,
                "quality_issues": deterministic_issues,
                "mode": "deterministic",
                "tokens": 0,
            }
            await control.emit(
                {
                    "type": "workflow_intent_validation_completed",
                    **validation,
                }
            )
            return final_output, validation

        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "chat",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
        if not endpoint:
            result = {
                "passed": False,
                "score": 0,
                "issues": ["没有启用的在线模型接口，无法执行最终意图校验"],
                "improved": False,
            }
            await control.emit(
                {
                    "type": "workflow_intent_validation_skipped",
                    **result,
                }
            )
            return final_output, result

        await control.emit(
            {
                "type": "workflow_intent_validation_started",
                "endpoint": endpoint.name,
                "model": endpoint.default_model,
            }
        )
        node_evidence = intent_prompt_value(
            context.get("nodes", {}),
            string_limit=12000,
        )
        payload = {
            "workflow": {
                "name": workflow.name,
                "description": workflow.description,
            },
            "user_intent": workflow_input.get("task") or workflow_input,
            "current_result": intent_prompt_value(
                final_output,
                string_limit=32000,
            ),
            "node_results": node_evidence,
            "deterministic_quality_issues": deterministic_issues,
        }
        system_prompt = (
            "你是工作流最终交付审校器。判断当前结果是否直接完成用户原始意图，而不是只输出"
            "提纲、审核意见、过程说明或中间材料。必须保留有依据的事实，禁止补造来源。"
            "若不符合，综合节点结果生成可直接交付给用户的修正版。只输出严格 JSON："
            '{"passed":true,"score":0-100,"issues":["问题"],'
            '"final_result":"直接交付正文"}。无论是否通过，final_result 都必须包含最终可交付正文。'
        )
        try:
            response = await provider_from_endpoint(endpoint).chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": dumps(payload)},
                ],
                model=endpoint.default_model,
                temperature=0.1,
                max_output_tokens=16000,
            )
            parsed = self._extract_json_object(response.content)
            if not parsed:
                raise ValueError("意图校验接口没有返回有效 JSON")
            passed = bool(parsed.get("passed"))
            try:
                score = max(0, min(int(parsed.get("score", 0)), 100))
            except (TypeError, ValueError):
                score = 0
            issues = [
                str(item)[:300] for item in (parsed.get("issues") or []) if str(item).strip()
            ][:12]
            improved_result = parsed.get("final_result")
            improved_text = str(improved_result or "").strip()
            original_text = primary_output_text(final_output).strip()
            preserves_substance = (
                not original_text
                or len(original_text) <= 8000
                or len(improved_text) >= int(len(original_text) * 0.6)
            )
            improved = bool(
                (not passed or payload["deterministic_quality_issues"])
                and improved_text
                and preserves_substance
            )
            if not passed and improved_text and not preserves_substance:
                issues.append("修正版篇幅异常缩短，已保留原始交付内容以避免信息丢失")
            if improved:
                if isinstance(final_output, dict) and "result" in final_output:
                    final_output = {**final_output, "result": improved_result}
                else:
                    final_output = improved_result
            quality_issues = self._delivery_quality_issues(workflow_input, final_output, context)
            issues = list(dict.fromkeys([*issues, *quality_issues]))[:12]
            passed = bool(passed and not quality_issues)
            improved = bool(improved and not quality_issues)
            validation = {
                "passed": passed,
                "score": score,
                "issues": issues,
                "improved": improved,
                "quality_issues": quality_issues,
                "endpoint": endpoint.name,
                "model": endpoint.default_model,
                "tokens": response.tokens,
            }
            await control.emit(
                {
                    "type": "workflow_intent_validation_completed",
                    **validation,
                }
            )
            return final_output, validation
        except Exception as exc:
            quality_issues = self._delivery_quality_issues(workflow_input, final_output, context)
            validation = {
                "passed": False,
                "score": 0,
                "issues": list(dict.fromkeys([str(exc)[:500], *quality_issues]))[:12],
                "improved": False,
                "quality_issues": quality_issues,
            }
            await control.emit(
                {
                    "type": "workflow_intent_validation_failed",
                    "error": str(exc)[:500],
                }
            )
            return final_output, validation

    async def control(self, run_id: str, action: str, message: str = "") -> dict[str, Any]:
        control = self.controls.get(run_id)
        if not control:
            return {"accepted": False, "run_id": run_id, "status": "not_running"}
        if action == "pause":
            control.gate.clear()
            await control.emit({"type": "workflow_pause_requested"})
            status = "pausing"
        elif action == "resume":
            control.gate.set()
            await control.emit({"type": "workflow_run_resumed"})
            status = "running"
        elif action == "interrupt":
            control.interrupted = True
            control.gate.set()
            await control.emit({"type": "workflow_interrupt_requested"})
            status = "interrupting"
        elif action == "guide":
            control.guidance.append(message.strip())
            await control.emit({"type": "workflow_guidance_received", "message": message.strip()})
            status = "guided"
        else:
            raise ValueError(f"不支持的运行控制动作：{action}")
        return {"accepted": True, "run_id": run_id, "status": status}

    async def _checkpoint(
        self,
        db: AsyncSession,
        run: WorkflowRun,
        control: WorkflowControl,
        context: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> None:
        if control.interrupted:
            raise WorkflowInterrupted("用户已中断工作流")
        if not control.gate.is_set():
            run.status = "paused"
            run.trace_json = dumps(trace)
            run.control_json = dumps(context.get("runtime", {}))
            await db.commit()
            await control.emit({"type": "workflow_run_paused", "node_id": run.current_node_id})
            await control.gate.wait()
            if control.interrupted:
                raise WorkflowInterrupted("用户已中断工作流")
            run.status = "running"
            run.control_json = dumps(context.get("runtime", {}))
            await db.commit()
        if control.guidance:
            messages = control.guidance[:]
            control.guidance.clear()
            context["runtime"]["guidance"].extend(messages)
            event = {
                "type": "guidance",
                "status": "applied",
                "messages": messages,
                "iteration": context["runtime"]["iteration"],
            }
            trace.append(event)
            run.trace_json = dumps(trace)
            run.control_json = dumps(context.get("runtime", {}))
            await db.commit()
            await control.emit({"type": "workflow_guidance_applied", **event})

    @staticmethod
    def _edge_enabled(edge: dict[str, Any], result: Any, node_type: str) -> bool:
        source_slot = str(
            edge.get("source_slot") or edge.get("sourceHandle") or edge.get("branch") or "output"
        )
        if node_type == "condition" and source_slot in {"true", "false"}:
            return str(result.get("route", "false")) == source_slot
        edge_condition = edge.get("condition")
        if isinstance(edge_condition, dict):
            left = render_value(edge_condition.get("left"), {"result": result})
            return _condition(
                left, str(edge_condition.get("operator", "equals")), edge_condition.get("right")
            )
        return True

    @staticmethod
    def _result_summary(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            output = result.get("output", result)
            summary = {
                key: compact_runtime_value(result[key])
                for key in (
                    "status",
                    "run_id",
                    "artifact_id",
                    "title",
                    "chunk_count",
                    "knowledge_base_name",
                    "route",
                    "passed",
                )
                if key in result
            }
        else:
            output = result
            summary = {}
        preview = output if isinstance(output, str) else dumps(output)
        summary["output_preview"] = preview if len(preview) <= 1800 else f"{preview[:1800]}…"
        return summary

    async def _create_artifact(
        self,
        db: AsyncSession,
        workflow: Workflow,
        run: WorkflowRun,
        *,
        iteration: int,
        title: str,
        content: str,
        node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowArtifact:
        artifact = WorkflowArtifact(
            workflow_id=workflow.id,
            run_id=run.id,
            node_id=node_id,
            iteration=iteration,
            title=title[:255],
            content=content,
            metadata_json=dumps(metadata or {}),
        )
        db.add(artifact)
        await db.flush()
        return artifact

    @staticmethod
    def _artifact_content(
        workflow: Workflow,
        workflow_input: dict[str, Any],
        output: Any,
        iteration: int,
        total: int,
        guidance: list[str],
    ) -> str:
        rendered_output = output_to_markdown(output)
        guidance_section = (
            "\n## 运行中人工引导\n\n" + "\n".join(f"- {item}" for item in guidance)
            if guidance
            else ""
        )
        return (
            f"# {workflow.name} · 第 {iteration}/{total} 轮产出\n\n"
            f"## 任务输入\n\n{workflow_input.get('task') or dumps(workflow_input)}\n\n"
            f"## 执行结果\n\n{rendered_output}"
            f"{guidance_section}\n\n"
            f"---\n\n工作流版本：v{workflow.version} · 运行编号：{iteration}/{total}"
        )

    async def _execute_node(
        self,
        db: AsyncSession,
        workflow: Workflow,
        run: WorkflowRun,
        node: dict[str, Any],
        config: dict[str, Any],
        context: dict[str, Any],
        active_parent_ids: list[str],
        on_agent_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> Any:
        node_type = node.get("type", "agent")
        node_label = str(node.get("label") or node["id"])
        workflow_input = context["input"]
        if node_type == "input":
            return workflow_input
        if node_type == "agent":
            original_intent = str(workflow_input.get("task") or dumps(workflow_input)).strip()
            if config.get("auto_input", False) and active_parent_ids:
                parent_inputs: list[str] = []
                for parent_id in active_parent_ids:
                    parent = context["nodes"].get(parent_id)
                    if isinstance(parent, dict):
                        parent_value = parent.get("output", parent.get("task", parent))
                    else:
                        parent_value = parent
                    text = parent_value if isinstance(parent_value, str) else dumps(parent_value)
                    if str(text).strip():
                        parent_inputs.append(f"【上游节点：{parent_id}】\n{str(text).strip()}")
                routed_input = "\n\n".join(parent_inputs).strip()
            else:
                routed_input = str(config.get("input", workflow_input.get("task", ""))).strip()
            node_prompt = str(config.get("prompt") or "").strip()
            if self.prompt_looks_corrupted(node_prompt):
                node_prompt = self.default_agent_node_prompt(node_label)
            tool_policy = self.agent_node_tool_policy(node_label, config)
            rag_policy = self.agent_node_rag_policy(node_label, config)
            context_limit = self.agent_node_context_limit(node_label, config)
            original_intent, original_removed = self._bounded_node_text(
                original_intent, min(8000, max(3000, context_limit // 4))
            )
            node_prompt, prompt_removed = self._bounded_node_text(
                node_prompt, min(12000, max(4000, context_limit // 3))
            )
            routed_budget = max(4000, context_limit - len(original_intent) - len(node_prompt) - 800)
            routed_input, routed_removed = self._bounded_node_text(
                routed_input or original_intent, routed_budget
            )
            node_input = (
                f"【用户原始意图】\n{original_intent}\n\n"
                f"【当前工作流节点】\n{node_label}\n\n"
                f"【本节点收到的输入】\n{routed_input or original_intent}\n\n"
                "【执行约束】\n"
                "严格服务于用户原始意图，不能把中间过程误当成最终目标。"
                "输出必须完整、可供下游节点直接使用；事实和引用不得脱离上游材料。"
            )
            if node_prompt:
                node_input += f"\n\n【节点专用任务说明】\n{node_prompt}"
            guidance = context["runtime"]["guidance"]
            if guidance:
                node_input += "\n\n【用户运行中引导】\n" + "\n".join(guidance)
            node_input, final_removed = self._bounded_node_text(node_input, context_limit)
            removed_chars = original_removed + prompt_removed + routed_removed + final_removed
            if on_agent_event:
                await on_agent_event(
                    {
                        "type": "node_context_prepared",
                        "tool_policy": tool_policy["preset"],
                        "rag_mode": rag_policy["mode"],
                        "context_chars": len(node_input),
                        "context_char_limit": context_limit,
                        "removed_chars": removed_chars,
                        "auto_input": bool(config.get("auto_input", False)),
                        "upstream_nodes": active_parent_ids,
                    }
                )
            runtime = context.get("runtime", {})
            permission_mode = str(runtime.get("permission_mode") or "inherit")
            agent_run = await agent_engine.run(
                db,
                str(config["agent_id"]),
                node_input,
                user_context={
                    "security_profile": str(runtime.get("security_profile") or "default"),
                },
                execution=ExecutionContext(
                    approval_run_id=run.id,
                    permission_mode=(permission_mode if permission_mode != "inherit" else None),
                    approval_policy_id=(str(runtime.get("approval_policy_id") or "") or None)
                    if permission_mode == "inherit"
                    else None,
                ),
                on_event=on_agent_event,
                max_output_tokens=(
                    int(config["max_output_tokens"])
                    if config.get("max_output_tokens") not in (None, "")
                    else None
                ),
                tool_policy=tool_policy,
                rag_policy=rag_policy,
            )
            agent_trace = loads(agent_run.trace_json, [])
            research_events = [
                item
                for item in agent_trace
                if isinstance(item, dict) and item.get("type") == "research_sources_selected"
            ]
            research_count = max(
                (int(item.get("count") or 0) for item in research_events),
                default=0,
            )
            result = {
                "status": agent_run.status,
                "output": agent_run.output_text,
                "run_id": agent_run.id,
                "error": agent_run.error,
                "research": {
                    "source_count": research_count,
                    "live": bool(research_count),
                },
            }
            if agent_run.status == "failed":
                raise RuntimeError(
                    f"Agent 节点“{node_label}”执行失败："
                    f"{agent_run.error or '模型请求未返回错误详情'}"
                )
            return result
        if node_type == "knowledge":
            knowledge_base_id = str(config.get("knowledge_base_id") or "")
            knowledge_base = (
                await db.get(KnowledgeBase, knowledge_base_id) if knowledge_base_id else None
            )
            if not knowledge_base:
                raise LookupError(f"知识库节点“{node_label}”绑定的知识库不存在")
            query = str(
                config.get("query") or config.get("input") or workflow_input.get("task", "")
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
            return {
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
        if node_type == "tool":
            runtime = context.get("runtime", {})
            run_security_profile = str(
                runtime.get("security_profile") or config.get("security_profile") or "default"
            )
            security = await runtime_security_service.resolve(db, run_security_profile)
            run_permission_mode = str(runtime.get("permission_mode") or "inherit")
            if run_permission_mode != "inherit":
                security.command_mode = (
                    "always_ask" if run_permission_mode == "ask" else run_permission_mode
                )
            permission_mode = (
                run_permission_mode
                if run_permission_mode != "inherit"
                else str(config.get("permission_mode", "ask"))
            )
            result = await tool_runtime.execute(
                db,
                str(config["tool"]),
                dict(config.get("arguments", {})),
                run_id=run.id,
                policy_id=(str(runtime.get("approval_policy_id") or "") or None)
                if run_permission_mode == "inherit"
                else None,
                permission_mode=permission_mode,
                security_context=security,
            )
            if result.get("status") != "approval_required":
                return result
            await db.commit()
            if on_agent_event:
                await on_agent_event(
                    {
                        "type": "approval_required",
                        "approval_id": result.get("approval_id"),
                        "tool": config["tool"],
                        "risk": result.get("risk"),
                        "message": result.get("message"),
                    }
                )
            resolved = await tool_runtime.wait_for_approval(str(result["approval_id"]))
            if on_agent_event:
                await on_agent_event(
                    {
                        "type": "approval_resolved",
                        "approval_id": result.get("approval_id"),
                        "tool": config["tool"],
                        "status": resolved.get("status"),
                    }
                )
            return resolved
        if node_type == "condition":
            expression = str(config.get("condition") or "").strip()
            if expression:
                passed = _condition_expression(expression)
                return {
                    "passed": passed,
                    "route": "true" if passed else "false",
                    "output": expression,
                    "expression": expression,
                }
            left = config.get("left")
            operator = str(config.get("operator", "equals"))
            right = config.get("right")
            passed = _condition(left, operator, right)
            return {
                "passed": passed,
                "route": "true" if passed else "false",
                "output": left,
                "left": left,
                "operator": operator,
                "right": right,
            }
        if node_type == "variable":
            assignments = config.get("assignments", [])
            changed: dict[str, Any] = {}
            for assignment in assignments:
                name = str(assignment.get("name") or "").strip()
                if not name:
                    continue
                value = assignment.get("value")
                operation = str(assignment.get("operation") or "set")
                current = context["variables"].get(name)
                if operation == "append":
                    value = [*(current if isinstance(current, list) else []), value]
                elif operation == "increment":
                    try:
                        value = float(current or 0) + float(value or 0)
                    except (TypeError, ValueError):
                        value = current
                context["variables"][name] = value
                changed[name] = value
            return {"output": changed, "variables": changed}
        if node_type == "template":
            output = str(config.get("template") or "")
            return {"output": output}
        if node_type == "function":
            output = _safe_function(
                str(config.get("function") or "concat"), config.get("arguments", [])
            )
            return {"output": output}
        if node_type == "merge":
            values = [
                context["nodes"][node_id]
                for node_id in active_parent_ids
                if node_id in context["nodes"]
            ]
            mode = str(config.get("mode") or "text")
            if mode == "list":
                output: Any = values
            elif mode == "object":
                output = {
                    node_id: context["nodes"][node_id]
                    for node_id in active_parent_ids
                    if node_id in context["nodes"]
                }
            else:
                output = str(config.get("separator", "\n\n")).join(
                    str(value.get("output", value) if isinstance(value, dict) else value)
                    for value in values
                )
            return {"output": output, "items": values}
        if node_type == "artifact":
            content_value = config.get("content", "")
            content = content_value if isinstance(content_value, str) else dumps(content_value)
            artifact = await self._create_artifact(
                db,
                workflow,
                run,
                iteration=context["runtime"]["iteration"],
                title=str(config.get("title") or f"{workflow.name} 产出文档"),
                content=content,
                node_id=node["id"],
                metadata={"source": "artifact_node"},
            )
            return {"output": content, "artifact_id": artifact.id, "title": artifact.title}
        if node_type == "output":
            return config.get("value", context["nodes"])
        raise ValueError(f"不支持的节点类型: {node_type}")

    async def run(
        self,
        db: AsyncSession,
        workflow_id: str,
        workflow_input: dict[str, Any],
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        run_options: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        workflow = await db.get(Workflow, workflow_id)
        if not workflow or not workflow.enabled:
            raise LookupError("工作流不存在或未启用")
        definition = loads(workflow.definition_json, {"nodes": [], "edges": []})
        await self.validate_runtime_definition(db, definition)
        run = WorkflowRun(
            workflow_id=workflow.id,
            status="running",
            input_json=dumps(workflow_input),
        )
        db.add(run)
        await db.flush()
        control = WorkflowControl(run.id, on_event)
        self.controls[run.id] = control
        await control.emit({"type": "workflow_run_started"})
        await db.commit()
        started = time.perf_counter()
        trace: list[dict[str, Any]] = []
        context: dict[str, Any] = {}
        try:
            execution = dict(definition.get("execution") or {})
            execution.update(
                {key: value for key, value in (run_options or {}).items() if value is not None}
            )
            loop_enabled = bool(execution.get("loop_enabled", False))
            total_iterations = max(
                1, min(int(execution.get("loop_count", 1) if loop_enabled else 1), 50)
            )
            artifact_enabled = bool(execution.get("artifact_enabled", True))
            variable_definitions = definition.get("variables", [])
            variables = {
                str(item.get("name")): item.get("default")
                for item in variable_definitions
                if item.get("name")
            }
            variables.update(
                workflow_input.get("variables", {})
                if isinstance(workflow_input.get("variables"), dict)
                else {}
            )
            context = {
                "input": workflow_input,
                "variables": variables,
                "nodes": {},
                "runtime": {
                    "iteration": 1,
                    "total_iterations": total_iterations,
                    "previous_output": None,
                    "guidance": [],
                    "security_profile": str(execution.get("security_profile") or "default"),
                    "permission_mode": str(execution.get("permission_mode") or "inherit"),
                    "approval_policy_id": str(execution.get("approval_policy_id") or ""),
                },
            }
            run.control_json = dumps(context["runtime"])
            await db.commit()
            ordered_nodes = self._order_nodes(definition)
            edges = definition.get("edges", [])
            final_output: Any = {}
            for iteration in range(1, total_iterations + 1):
                context["nodes"] = {}
                context["runtime"]["iteration"] = iteration
                run.iteration_count = iteration
                await self._checkpoint(db, run, control, context, trace)
                await control.emit(
                    {
                        "type": "workflow_iteration_started",
                        "iteration": iteration,
                        "total_iterations": total_iterations,
                    }
                )
                edge_active: dict[int, bool] = {}
                for node in ordered_nodes:
                    node_id = node["id"]
                    node_type = node.get("type", "agent")
                    node_label = str(node.get("label") or node_id)
                    incoming = [
                        (index, edge)
                        for index, edge in enumerate(edges)
                        if edge.get("target") == node_id
                    ]
                    active_incoming = [
                        edge for index, edge in incoming if edge_active.get(index, False)
                    ]
                    if incoming and not active_incoming:
                        skipped = {
                            "node_id": node_id,
                            "type": node_type,
                            "status": "skipped",
                            "iteration": iteration,
                            "reason": "上游分支未命中",
                        }
                        trace.append(skipped)
                        run.trace_json = dumps(trace)
                        run.control_json = dumps(context["runtime"])
                        await db.commit()
                        await control.emit(
                            {
                                "type": "workflow_node_skipped",
                                "node_id": node_id,
                                "node_type": node_type,
                                "label": node_label,
                                "iteration": iteration,
                                "reason": skipped["reason"],
                            }
                        )
                        for index, edge in enumerate(edges):
                            if edge.get("source") == node_id:
                                edge_active[index] = False
                        continue
                    run.current_node_id = node_id
                    await self._checkpoint(db, run, control, context, trace)
                    run.trace_json = dumps(trace)
                    run.control_json = dumps(context["runtime"])
                    await db.commit()
                    config = render_value(node.get("config", {}), context)
                    started_node = time.perf_counter()
                    await control.emit(
                        {
                            "type": "workflow_node_started",
                            "node_id": node_id,
                            "node_type": node_type,
                            "label": node_label,
                            "iteration": iteration,
                        }
                    )
                    active_parent_ids = [str(edge.get("source")) for edge in active_incoming]

                    async def publish_agent_event(
                        agent_event: dict[str, Any],
                        *,
                        current_node_id: str = node_id,
                        current_node_label: str = node_label,
                        current_iteration: int = iteration,
                    ) -> None:
                        await control.emit(
                            {
                                "type": "workflow_agent_event",
                                "node_id": current_node_id,
                                "node_type": "agent",
                                "label": current_node_label,
                                "iteration": current_iteration,
                                "agent_event": compact_runtime_value(agent_event),
                            }
                        )

                    # The online provider already performs cost-aware transport retries.
                    # Re-running a whole Agent node can duplicate a billable generation.
                    default_retries = 0
                    try:
                        retry_count = max(
                            0,
                            min(int(config.get("retry_count", default_retries)), 3),
                        )
                    except (TypeError, ValueError):
                        retry_count = default_retries
                    result: Any = None
                    for attempt in range(1, retry_count + 2):
                        try:
                            result = await self._execute_node(
                                db,
                                workflow,
                                run,
                                node,
                                config,
                                context,
                                active_parent_ids,
                                on_agent_event=(
                                    publish_agent_event if node_type in {"agent", "tool"} else None
                                ),
                            )
                            break
                        except Exception as exc:
                            if attempt <= retry_count and self._retryable_node_error(exc):
                                delay_ms = min(3000, 500 * attempt)
                                await control.emit(
                                    {
                                        "type": "workflow_node_retrying",
                                        "node_id": node_id,
                                        "node_type": node_type,
                                        "label": node_label,
                                        "iteration": iteration,
                                        "attempt": attempt + 1,
                                        "max_attempts": retry_count + 1,
                                        "delay_ms": delay_ms,
                                        "error": str(exc)[:500],
                                    }
                                )
                                await asyncio.sleep(delay_ms / 1000)
                                continue
                            node_error = (
                                str(exc).strip() or f"{type(exc).__name__}：节点执行未返回错误详情"
                            )
                            failed_event = {
                                "node_id": node_id,
                                "type": node_type,
                                "status": "failed",
                                "iteration": iteration,
                                "duration_ms": int((time.perf_counter() - started_node) * 1000),
                                "error": node_error,
                            }
                            trace.append(failed_event)
                            run.trace_json = dumps(trace)
                            run.control_json = dumps(context["runtime"])
                            await db.commit()
                            await control.emit(
                                {
                                    **failed_event,
                                    "type": "workflow_node_failed",
                                    "node_type": node_type,
                                    "label": node_label,
                                }
                            )
                            raise
                    context["nodes"][node_id] = result
                    completed_event = {
                        "node_id": node_id,
                        "type": node_type,
                        "status": "completed",
                        "iteration": iteration,
                        "duration_ms": int((time.perf_counter() - started_node) * 1000),
                        "result": self._result_summary(result),
                    }
                    trace.append(completed_event)
                    run.trace_json = dumps(trace)
                    run.control_json = dumps(context["runtime"])
                    await db.commit()
                    await control.emit(
                        {
                            **completed_event,
                            "type": "workflow_node_completed",
                            "node_type": node_type,
                            "label": node_label,
                        }
                    )
                    for index, edge in enumerate(edges):
                        if edge.get("source") == node_id:
                            edge_active[index] = self._edge_enabled(edge, result, node_type)
                output_nodes = [
                    node for node in definition.get("nodes", []) if node.get("type") == "output"
                ]
                final_output = (
                    context["nodes"].get(output_nodes[-1]["id"], {})
                    if output_nodes
                    else context["nodes"]
                )
                intent_validation = bool(
                    execution.get(
                        "intent_validation",
                        settings.require_online_agents,
                    )
                )
                if intent_validation:
                    final_output, validation = await self._align_result_with_intent(
                        db,
                        workflow,
                        workflow_input,
                        final_output,
                        context,
                        control,
                    )
                    context["runtime"]["intent_validation"] = validation
                    trace.append(
                        {
                            "type": "intent_validation",
                            "status": (
                                "completed"
                                if validation.get("passed") or validation.get("improved")
                                else "warning"
                            ),
                            "iteration": iteration,
                            **validation,
                        }
                    )
                context["runtime"]["previous_output"] = final_output
                if artifact_enabled:
                    quality_issues = list(
                        context["runtime"].get("intent_validation", {}).get("quality_issues", [])
                    )
                    artifact = await self._create_artifact(
                        db,
                        workflow,
                        run,
                        iteration=iteration,
                        title=f"{workflow.name} · 第 {iteration} 轮产出",
                        content=self._artifact_content(
                            workflow,
                            workflow_input,
                            final_output,
                            iteration,
                            total_iterations,
                            context["runtime"]["guidance"],
                        ),
                        metadata={
                            "source": "workflow_iteration",
                            "workflow_version": workflow.version,
                            "delivery_status": ("needs_revision" if quality_issues else "ready"),
                            "quality_issues": quality_issues,
                        },
                    )
                    await control.emit(
                        {
                            "type": "workflow_artifact_created",
                            "artifact_id": artifact.id,
                            "title": artifact.title,
                            "iteration": iteration,
                        }
                    )
                quality_issues = list(
                    context["runtime"].get("intent_validation", {}).get("quality_issues", [])
                )
                if quality_issues:
                    await control.emit(
                        {
                            "type": "workflow_delivery_quality_failed",
                            "iteration": iteration,
                            "issues": quality_issues,
                        }
                    )
                    raise RuntimeError("最终交付质量校验未通过：" + "；".join(quality_issues))
                await control.emit(
                    {
                        "type": "workflow_iteration_completed",
                        "iteration": iteration,
                        "total_iterations": total_iterations,
                    }
                )
                stop_condition = execution.get("stop_condition")
                if stop_condition and _truthy(render_value(stop_condition, context)):
                    await control.emit(
                        {
                            "type": "workflow_loop_stopped",
                            "iteration": iteration,
                            "reason": "停止条件已满足",
                        }
                    )
                    break
            run.status = "completed"
            run.current_node_id = None
            run.output_json = dumps(final_output)
            run.trace_json = dumps(trace)
            run.control_json = dumps(
                {
                    **context["runtime"],
                    "loop_enabled": loop_enabled,
                    "requested_iterations": total_iterations,
                    "intent_validation": context["runtime"].get("intent_validation", {}),
                }
            )
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await audit(db, "workflow.completed", "workflow_run", run.id)
            await control.emit(
                {
                    "type": "workflow_run_completed",
                    "duration_ms": run.duration_ms,
                    "iteration_count": run.iteration_count,
                }
            )
        except WorkflowInterrupted as exc:
            run.status = "interrupted"
            run.error = str(exc)
            run.output_json = dumps(context.get("nodes", {}))
            run.trace_json = dumps(trace + [{"status": "interrupted", "error": str(exc)}])
            run.control_json = dumps(context.get("runtime", {}))
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await audit(
                db,
                "workflow.interrupted",
                "workflow_run",
                run.id,
                {"error": str(exc)},
                success=False,
            )
            await control.emit({"type": "workflow_run_interrupted", "error": str(exc)})
        except Exception as exc:
            error_message = str(exc).strip() or f"{type(exc).__name__}：执行未返回错误详情"
            run.status = "failed"
            run.error = error_message
            run.output_json = dumps(context.get("nodes", {}))
            run.trace_json = dumps(trace + [{"status": "failed", "error": error_message}])
            run.control_json = dumps(context.get("runtime", {}))
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await audit(
                db,
                "workflow.failed",
                "workflow_run",
                run.id,
                {"error": error_message},
                success=False,
            )
            await control.emit({"type": "workflow_run_failed", "error": error_message})
        finally:
            self.controls.pop(run.id, None)
        await db.flush()
        return run


workflow_engine = WorkflowEngine()
