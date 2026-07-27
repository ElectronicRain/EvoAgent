from __future__ import annotations

import inspect
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    AgentArtifact,
    AgentDefinition,
    AgentRun,
    Extension,
    ModelEndpoint,
    Skill,
)
from ..schemas import AgentGenerationConfig, AgentRAGConfig
from .common import audit, dumps, loads
from .extensions import extension_service
from .intent import TaskIntent, intent_service
from .knowledge import knowledge_service
from .llm import get_provider, image_provider_from_endpoint, provider_from_endpoint
from .model_routing import resolve_agent_chat_endpoint
from .security import RuntimeSecurityContext, runtime_security_service
from .tools import tool_runtime
from .web_research import web_research_service


@dataclass
class ExecutionContext:
    depth: int = 0
    agent_stack: list[str] = field(default_factory=list)
    parent_run_id: str | None = None
    user_id: str | None = None
    reply_style_prompt: str = ""
    approval_run_id: str | None = None
    permission_mode: str | None = None
    approval_policy_id: str | None = None
    tool_policy: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgentToolPolicy:
    """A per-run tool budget so workflow roles do not inherit every Agent capability."""

    preset: str
    max_iterations: int
    max_calls: int
    result_char_limit: int
    context_char_limit: int
    allowed_tools: frozenset[str] | None
    allow_mcp: bool
    stop_on_repeated_call: bool
    allow_repair_retry: bool
    allow_quality_review: bool

    @classmethod
    def resolve(cls, value: dict[str, Any] | None = None) -> "AgentToolPolicy":
        raw = dict(value or {})
        preset = str(raw.get("preset") or "full").strip().lower()
        presets: dict[str, dict[str, Any]] = {
            "planning": {
                "max_iterations": 1,
                "max_calls": 0,
                "result_char_limit": 3000,
                "context_char_limit": 8000,
                "allowed_tools": [],
                "allow_mcp": False,
                "allow_repair_retry": False,
                "allow_quality_review": False,
            },
            "research": {
                # web_research is collected once by the deterministic research service;
                # it is intentionally not exposed as another model-callable tool.
                "max_iterations": 1,
                "max_calls": 0,
                "result_char_limit": 5000,
                "context_char_limit": 16000,
                "allowed_tools": ["web_research"],
                "allow_mcp": False,
                "allow_repair_retry": False,
                "allow_quality_review": False,
            },
            "writing": {
                "max_iterations": 1,
                "max_calls": 0,
                "result_char_limit": 4000,
                "context_char_limit": 12000,
                "allowed_tools": [],
                "allow_mcp": False,
                "allow_repair_retry": True,
                "allow_quality_review": False,
            },
            "review": {
                "max_iterations": 1,
                "max_calls": 0,
                "result_char_limit": 4000,
                "context_char_limit": 12000,
                "allowed_tools": [],
                "allow_mcp": False,
                "allow_repair_retry": False,
                "allow_quality_review": False,
            },
            "balanced": {
                "max_iterations": min(settings.max_tool_iterations, 3),
                "max_calls": 6,
                "result_char_limit": 6000,
                "context_char_limit": 20000,
                "allowed_tools": None,
                "allow_mcp": True,
                "allow_repair_retry": True,
                "allow_quality_review": True,
            },
            "full": {
                "max_iterations": settings.max_tool_iterations,
                "max_calls": 16,
                "result_char_limit": 8000,
                "context_char_limit": 32000,
                "allowed_tools": None,
                "allow_mcp": True,
                "allow_repair_retry": True,
                "allow_quality_review": True,
            },
        }
        if preset not in presets:
            preset = "balanced"
        resolved = {
            **presets[preset],
            **{key: item for key, item in raw.items() if item is not None},
        }
        allowed = resolved.get("allowed_tools")
        return cls(
            preset=preset,
            max_iterations=max(
                1,
                min(int(resolved.get("max_iterations", 1)), settings.max_tool_iterations),
            ),
            max_calls=max(0, min(int(resolved.get("max_calls", 0)), 64)),
            result_char_limit=max(1000, min(int(resolved.get("result_char_limit", 6000)), 24000)),
            context_char_limit=max(
                4000, min(int(resolved.get("context_char_limit", 20000)), 100000)
            ),
            allowed_tools=(
                frozenset(str(item) for item in allowed)
                if isinstance(allowed, (list, tuple, set, frozenset))
                else None
            ),
            allow_mcp=bool(resolved.get("allow_mcp", True)),
            stop_on_repeated_call=bool(resolved.get("stop_on_repeated_call", True)),
            allow_repair_retry=bool(resolved.get("allow_repair_retry", True)),
            allow_quality_review=bool(resolved.get("allow_quality_review", True)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "max_iterations": self.max_iterations,
            "max_calls": self.max_calls,
            "result_char_limit": self.result_char_limit,
            "context_char_limit": self.context_char_limit,
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
            "allow_mcp": self.allow_mcp,
            "stop_on_repeated_call": self.stop_on_repeated_call,
            "allow_repair_retry": self.allow_repair_retry,
            "allow_quality_review": self.allow_quality_review,
        }


class AgentEngine:
    research_role = re.compile(
        r"search|researcher|scholar|检索|搜索|查找|调查|调研|证据研究|资料搜集",
        re.I,
    )
    math_query_pattern = re.compile(
        r"(求解|证明|方程|函数|导数|微分|积分|极限|矩阵|向量|"
        r"解析几何|平面几何|立体几何|几何证明|三角形|三角函数|概率|"
        r"数列|不等式|解析式|抛物线|双曲线|椭圆|斜率|切线|渐近线|"
        r"[xyz]\s*[=<>]|\\frac|\\int|\\sum|\d+\s*[\+\-\*/\^]\s*\d+)",
        re.I,
    )
    image_request_pattern = re.compile(
        r"(生成|创建|画|绘制|设计|做)(一张|一个|幅)?[^，。；\n]{0,40}"
        r"(图片|图像|插画|示意图|效果图|海报|封面|概念图|场景图|流程图)|"
        r"(图片|图像|插画|海报|封面).{0,8}(生成|绘制|设计)",
        re.I,
    )

    def is_research_agent(self, agent: AgentDefinition) -> bool:
        profile = " ".join(
            [agent.slug, agent.name, agent.description or "", agent.system_prompt or ""]
        )
        return bool(self.research_role.search(profile))

    def is_math_query(self, query: str) -> bool:
        return bool(self.math_query_pattern.search(query))

    @staticmethod
    def _extract_image_prompt(content: str) -> tuple[str, str]:
        prompts = re.findall(
            r"```image-prompt\s*\n(.*?)\n```",
            content,
            flags=re.I | re.S,
        )
        cleaned = re.sub(
            r"\n*```image-prompt\s*\n.*?\n```\n*",
            "\n\n",
            content,
            flags=re.I | re.S,
        ).strip()
        prompt = "\n".join(item.strip() for item in prompts if item.strip())
        return cleaned, prompt[:4000]

    async def _image_endpoint(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
    ) -> ModelEndpoint | None:
        if agent.image_model_endpoint_id:
            endpoint = await db.get(ModelEndpoint, agent.image_model_endpoint_id)
            if endpoint and endpoint.enabled and endpoint.modality == "image":
                return endpoint
            return None
        return await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "image",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )

    @staticmethod
    async def _chat(
        provider: Any,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any:
        parameters = inspect.signature(provider.chat).parameters
        options: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "tools": tools,
        }
        if "top_p" in parameters:
            options["top_p"] = top_p
        if "max_output_tokens" in parameters:
            options["max_output_tokens"] = max_output_tokens
        return await provider.chat(messages, **options)

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        marker = f"\n…[已压缩 {len(value) - limit:,} 个字符]…\n"
        available = max(100, limit - len(marker))
        head = max(80, int(available * 0.72))
        tail = max(20, available - head)
        return f"{value[:head]}{marker}{value[-tail:]}"

    @classmethod
    def _tool_result_for_model(cls, result: dict[str, Any], limit: int) -> str:
        serialized = dumps(result)
        if len(serialized) <= limit:
            return serialized
        excerpt = cls._bounded_text(serialized, max(400, limit - 260))
        return dumps(
            {
                "status": result.get("status", "completed"),
                "tool": result.get("tool"),
                "truncated": True,
                "original_chars": len(serialized),
                "content_excerpt": excerpt,
                "instruction": "工具结果已按上下文预算压缩；请基于可见证据作答，不要重复调用相同参数。",
            }
        )

    @classmethod
    def _compact_tool_messages(
        cls, messages: list[dict[str, Any]], limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        copied = [dict(item) for item in messages]
        tool_indexes = [index for index, item in enumerate(copied) if item.get("role") == "tool"]
        total = sum(len(str(copied[index].get("content") or "")) for index in tool_indexes)
        if total <= limit:
            return copied, 0
        original_total = total
        for index in tool_indexes:
            if total <= limit:
                break
            content = str(copied[index].get("content") or "")
            target = max(500, len(content) - (total - limit))
            compacted = cls._bounded_text(content, target)
            copied[index]["content"] = compacted
            total -= len(content) - len(compacted)
        return copied, max(0, original_total - total)

    def call_agent_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "call_agent",
                "description": "调用工厂中的另一个 Agent 并取得结果",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_slug": {"type": "string"},
                        "input": {"type": "string"},
                    },
                    "required": ["agent_slug", "input"],
                },
            },
        }

    @staticmethod
    def rag_config(agent: AgentDefinition) -> AgentRAGConfig:
        return AgentRAGConfig.model_validate(loads(agent.rag_config_json, {}))

    @staticmethod
    def generation_config(agent: AgentDefinition) -> AgentGenerationConfig:
        return AgentGenerationConfig.model_validate(loads(agent.generation_config_json, {}))

    async def _standalone_rag_query(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
        query: str,
        history: list[dict[str, str]],
        config: AgentRAGConfig,
    ) -> str:
        relevant_history = [
            item
            for item in history[-config.max_history_messages :]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        if not config.multi_turn or not relevant_history:
            return query
        endpoint = (
            await db.get(ModelEndpoint, agent.model_endpoint_id)
            if agent.model_endpoint_id
            else None
        )
        if endpoint and endpoint.enabled:
            transcript = "\n".join(
                f"{'用户' if item['role'] == 'user' else '助手'}：{item['content']}"
                for item in relevant_history
            )
            try:
                response = await provider_from_endpoint(endpoint).chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "把当前追问改写成可独立检索的查询。必须继承对话中的实体、"
                                "限定条件和数量要求，不回答问题，只输出一行查询。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"对话：\n{transcript}\n\n当前追问：{query}",
                        },
                    ],
                    model=endpoint.default_model,
                    temperature=0,
                )
                standalone = re.sub(r"\s+", " ", response.content).strip()
                if standalone:
                    return standalone[:4000]
            except Exception:
                pass
        last_user = next(
            (
                str(item["content"]).strip()
                for item in reversed(relevant_history)
                if item["role"] == "user"
            ),
            "",
        )
        is_follow_up = len(query) <= 24 or bool(
            re.search(r"^(那|它|这个|这些|其中|上面|继续|还有|分别|全部|为什么)", query)
        )
        return f"{last_user}；追问：{query}"[:4000] if last_user and is_follow_up else query

    async def _prepare_agent_rag(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
        query: str,
        conversation_messages: list[dict[str, str]] | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        enabled_override: bool | None = None,
        query_rewrite_override: bool | None = None,
        cross_language_override: bool | None = None,
    ) -> dict[str, Any]:
        config = self.rag_config(agent)
        knowledge_base_ids = loads(agent.knowledge_bases_json, [])
        enabled = config.enabled if enabled_override is None else enabled_override
        if not enabled or not (knowledge_base_ids or config.knowledge_group_ids):
            return {
                "enabled": False,
                "query": query,
                "standalone_query": query,
                "rewritten_queries": [query],
                "chunks": [],
                "citations": [],
                "context": "",
                "trace": {"scope": "disabled"},
            }
        standalone = await self._standalone_rag_query(
            db,
            agent,
            query,
            conversation_messages or [],
            config,
        )
        if on_event:
            await on_event(
                {
                    "type": "query_condensed",
                    "original_query": query,
                    "standalone_query": standalone,
                    "changed": standalone != query,
                    "history_messages": len(conversation_messages or []),
                }
            )

        async def publish(event: dict[str, Any]) -> None:
            if on_event:
                await on_event(event)

        result = await knowledge_service.query(
            db,
            query=standalone,
            knowledge_base_ids=knowledge_base_ids,
            knowledge_group_ids=config.knowledge_group_ids,
            top_k=config.top_k,
            candidate_k=config.candidate_k,
            rerank_k=config.rerank_k,
            similarity_threshold=config.similarity_threshold,
            dense_weight=config.dense_weight,
            lexical_weight=config.lexical_weight,
            context_char_budget=config.context_char_budget,
            query_rewrite=(
                config.query_rewrite if query_rewrite_override is None else query_rewrite_override
            ),
            cross_language=(
                config.cross_language
                if cross_language_override is None
                else cross_language_override
            ),
            knowledge_graph=config.knowledge_graph,
            parent_expansion=config.parent_expansion,
            complete_list_expansion=config.complete_list_expansion,
            rerank_model=config.rerank_model,
            generate_answer=False,
            on_event=publish,
        )
        result.update(
            {
                "enabled": True,
                "original_query": query,
                "standalone_query": standalone,
                "settings": config.model_dump(),
            }
        )
        return result

    def _render_rag_prompt(
        self,
        agent: AgentDefinition,
        query: str,
        rag_result: dict[str, Any],
        conversation_messages: list[dict[str, str]] | None,
    ) -> str:
        generation = self.generation_config(agent)
        rag = self.rag_config(agent)
        history_rows = (conversation_messages or [])[-rag.max_history_messages :]
        history = "\n".join(
            f"{'用户' if item.get('role') == 'user' else '助手'}：{item.get('content', '')}"
            for item in history_rows
            if item.get("role") in {"user", "assistant"}
        )
        citations = "\n".join(
            f"[资料 {item['number']}] {item['title']} · {item['source']}"
            for item in rag_result.get("citations", [])
        )
        variables = {
            "question": query,
            "knowledge": rag_result.get("context") or "（未检索到可用证据）",
            "history": history or "（无历史对话）",
            "citations": citations or "（无可用引用）",
            **generation.custom_variables,
        }
        prompt = generation.prompt_template
        for key, value in variables.items():
            prompt = prompt.replace(f"{{{key}}}", str(value))
        if generation.grounded_refusal:
            prompt += (
                "\n\n证据不足时必须回答“当前知识库中未找到足以回答该问题的依据”，并指出缺失信息。"
            )
        return prompt

    async def preview_rag(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
        query: str,
        *,
        conversation_messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []

        async def capture(event: dict[str, Any]) -> None:
            trace.append(event)

        result = await self._prepare_agent_rag(
            db,
            agent,
            query,
            conversation_messages,
            capture,
        )
        result["rendered_prompt"] = self._render_rag_prompt(
            agent,
            query,
            result,
            conversation_messages,
        )
        result["pipeline"] = trace
        return result

    @staticmethod
    def _answer_quality_issues(
        answer: str,
        rag_result: dict[str, Any],
        generation: AgentGenerationConfig,
    ) -> list[str]:
        if not rag_result.get("enabled") or not rag_result.get("chunks"):
            return []
        issues: list[str] = []
        citation_numbers = {int(value) for value in re.findall(r"\[资料\s*(\d+)\]", answer)}
        valid_numbers = {int(item["number"]) for item in rag_result.get("citations", [])}
        if generation.citation_required and not citation_numbers:
            issues.append("回答没有使用 [资料 N] 引用")
        invalid = sorted(citation_numbers - valid_numbers)
        if invalid:
            issues.append(f"回答引用了不存在的资料编号：{invalid}")
        list_counts = [
            int(item.get("item_count") or 0)
            for item in rag_result.get("trace", {}).get("list_contexts", [])
        ]
        expected = max(list_counts, default=0)
        if expected >= 2 and rag_result.get("trace", {}).get("exhaustive_query"):
            missing = [
                number
                for number in range(1, expected + 1)
                if not re.search(rf"(^|\n)\s*{number}[.、)]", answer)
            ]
            if missing:
                issues.append(f"完整编号列表缺少第 {', '.join(map(str, missing))} 项")
        return issues

    async def _mcp_catalog(
        self, db: AsyncSession, permissions: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[Extension, str]], list[str], list[str]]:
        extensions = (
            await db.scalars(
                select(Extension).where(Extension.kind == "mcp", Extension.enabled.is_(True))
            )
        ).all()
        selected = permissions.get("mcp_extensions")
        if isinstance(selected, list):
            selected_ids = {str(item) for item in selected}
            extensions = [item for item in extensions if item.id in selected_ids]
        else:
            # Legacy Agents inherit the two local built-in MCP services. Custom remote
            # services are opt-in so a stale endpoint cannot delay every conversation.
            extensions = [item for item in extensions if extension_service._builtin_endpoint(item)]
        schemas: list[dict[str, Any]] = []
        bindings: dict[str, tuple[Extension, str]] = {}
        services: list[str] = []
        errors: list[str] = []
        for extension in extensions:
            try:
                result = await extension_service.list_mcp_tools(extension)
                tools = list(result.get("tools") or [])
            except Exception as exc:
                errors.append(f"{extension.name}: {str(exc)[:160]}")
                continue
            services.append(extension.name)
            extension_key = extension.id.replace("-", "")[:8]
            for item in tools:
                remote_name = str(item.get("name") or "").strip()
                if not remote_name:
                    continue
                safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_name).strip("_")
                exposed_name = f"mcp_{extension_key}_{safe_name}"[:64]
                bindings[exposed_name] = (extension, remote_name)
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": exposed_name,
                            "description": (
                                f"通过 MCP“{extension.name}”调用 {remote_name}："
                                f"{str(item.get('description') or '')}"
                            ),
                            "parameters": item.get("inputSchema")
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
        return schemas, bindings, services, errors

    async def run(
        self,
        db: AsyncSession,
        agent_id: str,
        input_text: str,
        user_context: dict[str, Any] | None = None,
        execution: ExecutionContext | None = None,
        conversation_messages: list[dict[str, str]] | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        max_output_tokens: int | None = None,
        tool_policy: dict[str, Any] | None = None,
        rag_policy: dict[str, Any] | None = None,
    ) -> AgentRun:
        execution = execution or ExecutionContext()
        if user_context:
            execution.user_id = str(user_context.get("user_id") or execution.user_id or "") or None
            execution.reply_style_prompt = str(
                user_context.get("reply_style_prompt") or execution.reply_style_prompt or ""
            )
        if execution.depth >= settings.max_agent_depth:
            raise RuntimeError("已达到 Agent 最大调用深度")
        agent = await db.get(AgentDefinition, agent_id)
        if not agent or agent.status not in {"active", "candidate"}:
            raise LookupError("Agent 不存在或未启用")
        if agent.id in execution.agent_stack:
            chain = " -> ".join(execution.agent_stack + [agent.id])
            raise RuntimeError(f"检测到 Agent 循环调用: {chain}")

        agent_permissions = loads(agent.permissions_json, {})
        runtime_tool_policy = AgentToolPolicy.resolve(tool_policy or execution.tool_policy)
        execution.tool_policy = runtime_tool_policy.as_dict()
        security_profile = str(
            (user_context or {}).get("security_profile")
            or agent_permissions.get("security_profile")
            or "default"
        )
        security_context = await runtime_security_service.resolve(db, security_profile)
        if execution.permission_mode:
            security_context.command_mode = (
                "always_ask" if execution.permission_mode == "ask" else execution.permission_mode
            )
        run = AgentRun(
            agent_id=agent.id,
            user_id=execution.user_id,
            parent_run_id=execution.parent_run_id,
            status="running",
            input_text=input_text,
            security_json=dumps(security_context.as_dict()),
        )
        db.add(run)
        await db.flush()
        started = time.perf_counter()
        trace: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            trace.append(event)
            run.trace_json = dumps(trace)
            # Persist each observable checkpoint before an external model/tool wait.
            # This keeps a slow custom API from holding SQLite's single writer lock.
            await db.commit()
            if on_event:
                await on_event(event)

        try:
            await emit(
                {
                    "type": "run_started",
                    "run_id": run.id,
                    "agent": agent.slug,
                    "depth": execution.depth,
                    "security": security_context.as_dict(),
                }
            )
            endpoint = await resolve_agent_chat_endpoint(db, agent)
            if endpoint:
                await db.flush()
            intent = intent_service.classify(input_text)
            local_request = intent.category in {
                "command_execution",
                "local_workspace_change",
                "local_file_access",
            }
            permissions = agent_permissions
            allowed_tools = set(loads(agent.tools_json, [])) | {"exec"}
            if runtime_tool_policy.allowed_tools is not None:
                allowed_tools.intersection_update(runtime_tool_policy.allowed_tools)
            research_requested = "web_research" in allowed_tools and (
                runtime_tool_policy.preset == "research"
                or (not local_request and intent.category == "web_research")
            )
            if runtime_tool_policy.allow_mcp and runtime_tool_policy.max_calls > 0:
                mcp_schemas, mcp_bindings, mcp_services, mcp_errors = await self._mcp_catalog(
                    db, permissions
                )
            else:
                mcp_schemas, mcp_bindings, mcp_services, mcp_errors = [], {}, [], []
            generation_config = self.generation_config(agent)
            effective_max_output_tokens = generation_config.max_output_tokens
            if max_output_tokens is not None:
                effective_max_output_tokens = max(128, min(int(max_output_tokens), 32768))
            image_endpoint = await self._image_endpoint(db, agent)
            math_query = self.is_math_query(input_text)

            async def publish_rag_step(event: dict[str, Any]) -> None:
                await emit({**event, "type": f"rag_{event['type']}"})

            runtime_rag_policy = dict(rag_policy or {})
            rag_mode = str(runtime_rag_policy.get("mode") or "agent").lower()
            rag_result = await self._prepare_agent_rag(
                db,
                agent,
                input_text,
                conversation_messages,
                publish_rag_step,
                enabled_override=(False if rag_mode == "off" else None),
                query_rewrite_override=runtime_rag_policy.get("query_rewrite"),
                cross_language_override=runtime_rag_policy.get("cross_language"),
            )
            system_prompt = await self._build_system_prompt(
                db,
                agent,
                input_text,
                security_context,
                local_request,
                mcp_services,
                intent,
                execution.reply_style_prompt,
                rag_result,
                conversation_messages,
                image_generation_available=bool(image_endpoint),
                math_query=math_query,
                available_tools=allowed_tools,
                tool_policy=runtime_tool_policy,
            )
            await emit({"type": "intent_detected", **intent.as_dict()})
            await emit(
                {
                    "type": "context_ready",
                    "knowledge_attached": bool(rag_result.get("chunks")),
                    "rag": {
                        "enabled": rag_result.get("enabled", False),
                        "standalone_query": rag_result.get("standalone_query", input_text),
                        "citations": len(rag_result.get("citations", [])),
                        "context_chars": len(rag_result.get("context", "")),
                    },
                    "history_messages": len(conversation_messages or []),
                    "capabilities": {
                        "exec": "exec" in allowed_tools,
                        "skills": "【已启用 Skills】" in system_prompt,
                        "mcp_services": mcp_services,
                        "image_generation": bool(image_endpoint),
                        "math_visualization": math_query,
                    },
                    "tool_policy": runtime_tool_policy.as_dict(),
                    "rag_policy": {
                        "mode": rag_mode,
                        "query_rewrite": runtime_rag_policy.get("query_rewrite"),
                        "cross_language": runtime_rag_policy.get("cross_language"),
                    },
                }
            )
            schemas = [
                item for item in tool_runtime.schemas() if item["function"]["name"] in allowed_tools
            ]
            if "call_agent" in allowed_tools:
                schemas.append(self.call_agent_schema())
            schemas.extend(mcp_schemas)
            if runtime_tool_policy.max_calls <= 0:
                schemas = []
            if mcp_errors:
                await emit({"type": "mcp_unavailable", "errors": mcp_errors})
            await emit(
                {
                    "type": "tool_policy_applied",
                    "preset": runtime_tool_policy.preset,
                    "max_iterations": runtime_tool_policy.max_iterations,
                    "max_calls": runtime_tool_policy.max_calls,
                    "result_char_limit": runtime_tool_policy.result_char_limit,
                    "context_char_limit": runtime_tool_policy.context_char_limit,
                    "available_tools": [item["function"]["name"] for item in schemas]
                    + (["web_research"] if research_requested else []),
                    "deterministic_research": research_requested,
                    "mcp_enabled": runtime_tool_policy.allow_mcp,
                }
            )
            provider = (
                provider_from_endpoint(endpoint) if endpoint else get_provider(agent.provider)
            )
            model_name = endpoint.default_model if endpoint else agent.model
            total_tokens = 0
            model_calls = 0
            final_content = ""
            research_sources: list[dict[str, Any]] = []
            if research_requested:
                research_sources = await web_research_service.collect(input_text, emit)
                target_source_count = web_research_service.explicit_source_count(input_text)
                if not research_sources:
                    await emit(
                        {
                            "type": "web_research_empty",
                            "message": "联网检索未取得可追溯来源，已停止模型生成以防止虚构资料。",
                        }
                    )
                if not research_sources:
                    await emit(
                        {
                            "type": "research_requirements_unmet",
                            "target_sources": target_source_count or 1,
                            "required_sources": target_source_count or 1,
                            "actual_sources": 0,
                            "message": "没有取得真实来源，未调用模型综合。",
                        }
                    )
                    raise RuntimeError(
                        "真实联网检索未取得可追溯来源；"
                        "已在模型生成前停止，避免基于零来源编造文献或继续消耗额度"
                    )
                target_shortfall = bool(
                    target_source_count and len(research_sources) < target_source_count
                )
                if target_shortfall:
                    await emit(
                        {
                            "type": "research_target_shortfall",
                            "target_sources": target_source_count,
                            "actual_sources": len(research_sources),
                            "message": "未达到偏好目标，但已有真实来源，将继续综合并披露实际数量。",
                        }
                    )
                research_context = web_research_service.context(
                    research_sources,
                    char_limit=runtime_tool_policy.context_char_limit,
                )
                system_prompt += (
                    "\n\n系统已经代表你完成了真实联网检索，以下资料来自本轮实时搜索。"
                    "必须基于这些来源回答并给出可点击链接；不得声称自己无法联网、网络访问受限，"
                    "也不得把模型训练记忆冒充本轮检索结果。"
                    "严禁添加 Gap Filler、Representative Work、To be verified 或任何占位文献；"
                    "文献表中的每一项必须与下方真实来源一一对应。\n\n" + research_context
                )
                if target_shortfall:
                    system_prompt += (
                        "\n\n本轮用户设置的是偏好目标，不是硬性成功门槛："
                        f"目标约 {target_source_count} 篇，实际取得 {len(research_sources)} 篇。"
                        "请继续完成任务，在摘要或方法部分如实披露实际纳入数量；"
                        "不得把缺少的数量补造成虚假文献。"
                    )
                await emit(
                    {
                        "type": "research_context_ready",
                        "sources": len(research_sources),
                        "context_chars": len(research_context),
                        "context_char_limit": runtime_tool_policy.context_char_limit,
                    }
                )
                await emit(
                    {
                        "type": "research_synthesis_started",
                        "sources": len(research_sources),
                    }
                )

            local_plan = (
                None if intent.needs_clarification else tool_runtime.plan_local_request(input_text)
            )
            if local_plan:
                planned_tool = str(local_plan["tool"])
                await emit(
                    {
                        "type": "local_intent_detected",
                        "tool": planned_tool,
                        "arguments": local_plan["arguments"],
                        "allowed": planned_tool in allowed_tools,
                    }
                )
                if planned_tool in allowed_tools:
                    try:
                        local_result = await self._execute_tool(
                            db,
                            agent,
                            run,
                            planned_tool,
                            local_plan["arguments"],
                            execution,
                            security_context,
                            mcp_bindings,
                        )
                        local_result = await self._publish_tool_result(
                            db, emit, planned_tool, local_result
                        )
                    except Exception as exc:
                        local_result = {
                            "status": "failed",
                            "tool": planned_tool,
                            "error": str(exc),
                        }
                    system_prompt += (
                        "\n\n【本地请求预检结果】\n"
                        f"工具：{planned_tool}\n结果：{dumps(local_result)}\n"
                        "应优先依据该本地结果回答；如果路径被安全策略拒绝，明确告诉用户应切换的"
                        "安全模式或需要添加的授权目录，不要改为联网搜索。"
                    )
                else:
                    system_prompt += (
                        "\n\n【本地工具权限不足】\n用户要求操作本地路径，但当前 Agent 未启用 "
                        f"{planned_tool}。请明确提示用户在 Agent 工厂开启该工具，不要改为联网搜索。"
                    )

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
            ]
            messages.extend(conversation_messages or [])
            messages.append({"role": "user", "content": input_text})

            tool_calls_requested = 0
            tool_calls_executed = 0
            tool_calls_reused = 0
            tool_cache: dict[str, dict[str, Any]] = {}
            convergence_reason = ""
            for iteration in range(runtime_tool_policy.max_iterations):
                model_messages, compacted_chars = self._compact_tool_messages(
                    messages, runtime_tool_policy.context_char_limit
                )
                if compacted_chars:
                    await emit(
                        {
                            "type": "tool_context_compacted",
                            "iteration": iteration + 1,
                            "removed_chars": compacted_chars,
                            "context_char_limit": runtime_tool_policy.context_char_limit,
                        }
                    )
                response = await self._chat(
                    provider,
                    model_messages,
                    model=model_name,
                    temperature=agent.temperature,
                    tools=schemas or None,
                    top_p=generation_config.top_p,
                    max_output_tokens=effective_max_output_tokens,
                )
                model_calls += 1
                total_tokens += response.tokens
                await emit(
                    {
                        "type": "model_response",
                        "iteration": iteration + 1,
                        "stage": "research_synthesis" if research_requested else "answer",
                        "tool_calls": [item["name"] for item in response.tool_calls],
                    }
                )
                if not response.tool_calls:
                    final_content = response.content
                    break
                assistant_tool_calls = []
                for item in response.tool_calls:
                    assistant_tool_calls.append(
                        {
                            "id": item["id"],
                            "type": "function",
                            "function": {
                                "name": item["name"],
                                "arguments": dumps(item["arguments"]),
                            },
                        }
                    )
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or None,
                        "tool_calls": assistant_tool_calls,
                    }
                )
                for item in response.tool_calls:
                    tool_calls_requested += 1
                    cache_key = f"{item['name']}:{dumps(item['arguments'])}"
                    if cache_key in tool_cache:
                        result = tool_cache[cache_key]
                        tool_calls_reused += 1
                        await emit(
                            {
                                "type": "tool_result_reused",
                                "tool": item["name"],
                                "reason": "相同参数已执行，本次直接复用结果",
                            }
                        )
                        if runtime_tool_policy.stop_on_repeated_call:
                            convergence_reason = "repeated_tool_call"
                    elif tool_calls_executed >= runtime_tool_policy.max_calls:
                        result = {
                            "status": "budget_exhausted",
                            "tool": item["name"],
                            "error": "本节点工具调用预算已用尽，请基于已有结果直接生成最终交付。",
                        }
                        convergence_reason = "tool_call_budget"
                    else:
                        try:
                            result = await self._execute_tool(
                                db,
                                agent,
                                run,
                                item["name"],
                                item["arguments"],
                                execution,
                                security_context,
                                mcp_bindings,
                            )
                        except Exception as exc:
                            # A recoverable tool problem (for example, a stale file path
                            # proposed by the model) must not abort the whole Agent run.
                            message = str(exc).strip() or f"{type(exc).__name__}：工具执行失败"
                            result = {
                                "status": "failed",
                                "error": message,
                                "tool": item["name"],
                                "recovery": "请检查参数；读取文件前先调用 list_directory，或跳过该文件继续任务。",
                            }
                        tool_calls_executed += 1
                        result = await self._publish_tool_result(db, emit, item["name"], result)
                        tool_cache[cache_key] = result
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": self._tool_result_for_model(
                                result, runtime_tool_policy.result_char_limit
                            ),
                        }
                    )
                await emit(
                    {
                        "type": "tool_budget_updated",
                        "iterations_used": iteration + 1,
                        "max_iterations": runtime_tool_policy.max_iterations,
                        "calls_requested": tool_calls_requested,
                        "calls_executed": tool_calls_executed,
                        "calls_reused": tool_calls_reused,
                        "max_calls": runtime_tool_policy.max_calls,
                    }
                )
                if convergence_reason:
                    break
            else:
                if not final_content:
                    convergence_reason = "tool_iteration_budget"

            if not final_content:
                await emit(
                    {
                        "type": "tool_iteration_limit_reached",
                        "iterations": runtime_tool_policy.max_iterations,
                        "calls_requested": tool_calls_requested,
                        "calls_executed": tool_calls_executed,
                        "calls_reused": tool_calls_reused,
                        "reason": convergence_reason or "tool_iteration_budget",
                        "message": "本节点已完成允许的资料调用，正在基于真实结果整理最终答案。",
                    }
                )
                recovery_messages, compacted_chars = self._compact_tool_messages(
                    messages, runtime_tool_policy.context_char_limit
                )
                if compacted_chars:
                    await emit(
                        {
                            "type": "tool_context_compacted",
                            "iteration": runtime_tool_policy.max_iterations + 1,
                            "removed_chars": compacted_chars,
                            "context_char_limit": runtime_tool_policy.context_char_limit,
                        }
                    )
                recovery = await self._chat(
                    provider,
                    recovery_messages
                    + [
                        {
                            "role": "user",
                            "content": (
                                "工具调用次数已经达到上限。禁止继续调用任何工具。"
                                "请立即基于以上工具返回的真实结果，直接完成用户最初要求的最终交付；"
                                "若证据仍不足，明确标注缺口，不得编造。"
                            ),
                        }
                    ],
                    model=model_name,
                    temperature=min(agent.temperature, 0.3),
                    tools=None,
                    top_p=generation_config.top_p,
                    max_output_tokens=effective_max_output_tokens,
                )
                model_calls += 1
                total_tokens += recovery.tokens
                final_content = recovery.content.strip()
                await emit(
                    {
                        "type": "tool_iteration_recovered",
                        "completed": bool(final_content),
                    }
                )
                if not final_content:
                    raise RuntimeError("Agent 已达到工具调用上限，模型仍未返回最终结果")

            if not research_requested and generation_config.verify_answer and final_content:
                await emit(
                    {
                        "type": "generation_verification_started",
                        "citations": len(rag_result.get("citations", [])),
                    }
                )
                quality_issues = self._answer_quality_issues(
                    final_content,
                    rag_result,
                    generation_config,
                )
                if (
                    quality_issues
                    and not endpoint
                    and generation_config.citation_required
                    and rag_result.get("citations")
                    and "[资料 " not in final_content
                ):
                    final_content = f"{final_content.rstrip()}\n\n[资料 1]"
                    quality_issues = self._answer_quality_issues(
                        final_content,
                        rag_result,
                        generation_config,
                    )
                if (
                    quality_issues
                    and endpoint
                    and generation_config.repair_retry
                    and runtime_tool_policy.allow_repair_retry
                ):
                    await emit(
                        {
                            "type": "generation_repair_started",
                            "issues": quality_issues,
                        }
                    )
                    repair = await self._chat(
                        provider,
                        messages
                        + [
                            {"role": "assistant", "content": final_content},
                            {
                                "role": "user",
                                "content": (
                                    "请只依据系统消息中的检索资料修复回答。"
                                    f"必须解决：{'；'.join(quality_issues)}。"
                                    "输出修复后的完整答案，不解释修复过程。"
                                ),
                            },
                        ],
                        model=model_name,
                        temperature=min(agent.temperature, 0.2),
                        top_p=generation_config.top_p,
                        max_output_tokens=effective_max_output_tokens,
                    )
                    model_calls += 1
                    total_tokens += repair.tokens
                    if repair.content.strip():
                        final_content = repair.content.strip()
                    quality_issues = self._answer_quality_issues(
                        final_content,
                        rag_result,
                        generation_config,
                    )
                    await emit(
                        {
                            "type": "generation_repaired",
                            "passed": not quality_issues,
                            "issues": quality_issues,
                        }
                    )
                await emit(
                    {
                        "type": "generation_verified",
                        "passed": not quality_issues,
                        "issues": quality_issues,
                        "citation_count": len(re.findall(r"\[资料\s*\d+\]", final_content)),
                    }
                )

            if research_requested and final_content and runtime_tool_policy.allow_quality_review:
                try:
                    await emit({"type": "quality_review_started", "sources": len(research_sources)})
                    review = await provider.chat(
                        messages
                        + [
                            {"role": "assistant", "content": final_content},
                            {
                                "role": "user",
                                "content": (
                                    f"请对任务“{input_text}”的上述综述进行第二轮审校：检查结构、"
                                    "来源可追溯性、事实与推断边界、局限性；将正文润色为正式、连贯的"
                                    "学术综述，统一专业术语，删除重复内容。使用规范 Markdown 标题、"
                                    "列表、表格与 LaTeX 公式，确保可直接渲染；输出修订后的完整正文，"
                                    "不要使用代码围栏包裹全文。"
                                ),
                            },
                        ],
                        model=model_name,
                        temperature=min(agent.temperature, 0.4),
                        max_output_tokens=effective_max_output_tokens,
                    )
                    model_calls += 1
                    total_tokens += review.tokens
                    if review.content.strip():
                        final_content = review.content.strip()
                    await emit(
                        {
                            "type": "model_response",
                            "iteration": 2,
                            "stage": "quality_review",
                            "tool_calls": [],
                        }
                    )
                except Exception as exc:
                    await emit({"type": "quality_review_skipped", "message": str(exc)[:240]})

            final_content, suggested_image_prompt = self._extract_image_prompt(final_content)
            explicit_image_request = bool(self.image_request_pattern.search(input_text)) and (
                not math_query or bool(re.search(r"(图片|插画|海报|封面|位图)", input_text))
            )
            should_generate_image = bool(suggested_image_prompt or explicit_image_request)
            if should_generate_image and image_endpoint:
                image_prompt = suggested_image_prompt or (
                    "根据以下用户需求生成一张信息准确、主体清晰、构图专业的图片。"
                    "图片中不要添加无法确认的文字、数字或品牌标识。\n\n"
                    f"用户需求：{input_text[:1600]}\n\n"
                    f"回答要点：{final_content[:1600]}"
                )
                try:
                    await emit(
                        {
                            "type": "image_generation_started",
                            "endpoint": image_endpoint.name,
                            "model": image_endpoint.default_model,
                        }
                    )
                    image = await image_provider_from_endpoint(image_endpoint).generate(
                        image_prompt,
                        model=image_endpoint.default_model,
                    )
                    if not re.match(
                        r"^(?:https?://|data:image/(?:png|jpe?g|webp);base64,)",
                        image.image_url,
                        re.I,
                    ):
                        raise RuntimeError("图片模型返回了不安全的图片地址")
                    final_content = (
                        f"{final_content.rstrip()}\n\n"
                        "## 生成图片\n\n"
                        f"![AI 生成图片](<{image.image_url}>)"
                    )
                    await emit(
                        {
                            "type": "image_generated",
                            "endpoint": image_endpoint.name,
                            "model": image_endpoint.default_model,
                            "image_url": image.image_url,
                            "revised_prompt": image.revised_prompt,
                        }
                    )
                except Exception as exc:
                    await emit(
                        {
                            "type": "image_generation_failed",
                            "message": str(exc)[:500],
                        }
                    )
                    if explicit_image_request:
                        final_content = (
                            f"{final_content.rstrip()}\n\n> 图片生成失败：{str(exc)[:240]}"
                        )
            elif explicit_image_request and not image_endpoint:
                final_content = (
                    f"{final_content.rstrip()}\n\n"
                    "> 尚未配置启用的图片生成模型接口；请在“扩展与模型”中添加图片模型。"
                )

            if research_requested:
                title = input_text.strip().replace("\n", " ")[:80] or "Agent 研究成果"
                references = "\n".join(
                    f"{index}. [{item['title']}]({item['url']}) · {item.get('source', 'Web')} · "
                    f"可信度 {(item.get('credibility') or {}).get('level', '待核验')} "
                    f"{(item.get('credibility') or {}).get('score', 0)}/100"
                    + (
                        " · [Google Scholar]("
                        f"{item.get('scholar_url') or web_research_service.scholar_url(item['title'])})"
                        if web_research_service.research_mode(input_text) == "academic"
                        else ""
                    )
                    for index, item in enumerate(research_sources, 1)
                )
                markdown = (
                    f"# {title}\n\n"
                    "> 由 EvoAgent 联网检索、多轮综合与质量审校生成。重要结论请人工复核。\n\n"
                    f"{final_content.strip()}\n\n"
                    "## 检索来源\n\n"
                    f"{references or '本次联网检索未取得可用来源，相关结论均应视为待核验。'}\n"
                )
                artifact = AgentArtifact(
                    run_id=run.id,
                    conversation_id=(user_context or {}).get("conversation_id"),
                    kind="markdown",
                    title=f"{title}.md",
                    relative_path="database://agent-artifacts/pending",
                    content=markdown,
                )
                db.add(artifact)
                await db.flush()
                artifact.relative_path = f"database://agent-artifacts/{artifact.id}"
                final_content = markdown
                await emit(
                    {
                        "type": "artifact_created",
                        "artifact_id": artifact.id,
                        "title": artifact.title,
                        "storage": "business_database",
                    }
                )

            run.status = "completed"
            run.output_text = final_content
            run.token_usage = total_tokens
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await emit(
                {
                    "type": "run_completed",
                    "duration_ms": run.duration_ms,
                    "token_usage": total_tokens,
                    "model_calls": model_calls,
                    "tool_calls_requested": tool_calls_requested,
                    "tool_calls_executed": tool_calls_executed,
                    "tool_calls_reused": tool_calls_reused,
                    "tool_policy": runtime_tool_policy.preset,
                }
            )
            await audit(
                db,
                "agent.completed",
                "agent_run",
                run.id,
                {"agent_id": agent.id, "duration_ms": run.duration_ms},
            )
        except Exception as exc:
            error_message = str(exc).strip() or f"{type(exc).__name__}：模型或工具未返回错误详情"
            run.status = "failed"
            run.error = error_message
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            await emit({"type": "error", "message": error_message})
            await audit(
                db,
                "agent.failed",
                "agent_run",
                run.id,
                {"error": error_message},
                success=False,
            )
        await db.flush()
        return run

    async def _build_system_prompt(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
        query: str,
        security: RuntimeSecurityContext,
        local_request: bool,
        mcp_services: list[str],
        intent: TaskIntent,
        reply_style_prompt: str = "",
        rag_result: dict[str, Any] | None = None,
        conversation_messages: list[dict[str, str]] | None = None,
        image_generation_available: bool = False,
        math_query: bool = False,
        available_tools: set[str] | None = None,
        tool_policy: AgentToolPolicy | None = None,
    ) -> str:
        available_tools = available_tools or set()
        parts = [
            agent.system_prompt,
            "你运行在 EvoAgent 中。关键结论必须说明依据；不确定时明确标注。",
            "所有输出均为 AI 生成内容，涉及高风险领域必须建议人工复核。",
            (
                f"本轮安全配置：文件系统模式 {security.filesystem_mode}；命令模式 "
                f"{security.command_mode}；授权根目录：{'；'.join(security.roots)}。"
            ),
        ]
        local_tools = available_tools.intersection(
            {"list_directory", "read_file", "search_files", "write_file", "run_powershell", "exec"}
        )
        if local_tools:
            parts.append(
                "本轮可用本地工具："
                + "、".join(sorted(local_tools))
                + "。仅当任务确实依赖本地事实或操作时调用；不要为确认已知信息而调用，"
                "不要使用相同参数重复调用。路径必须符合本轮安全配置。"
            )
        else:
            parts.append(
                "本节点不开放主动本地工具调用。请直接依据用户输入、上游结果与系统已附加的"
                "知识上下文完成本节点职责，不要索要或猜测工具结果。"
            )
        if tool_policy:
            parts.append(
                "【工具停止条件】\n"
                f"策略：{tool_policy.preset}；最多 {tool_policy.max_iterations} 轮、"
                f"{tool_policy.max_calls} 次模型工具请求。已有证据足以形成交付时必须立即停止调用并输出；"
                "不得重复查询相同参数，也不得为了扩写篇幅继续搜索。预算用尽时基于已有真实结果收敛，"
                "明确证据缺口，禁止编造。"
            )
        parts.append(intent_service.prompt(intent))
        if reply_style_prompt:
            parts.append(reply_style_prompt)
        if mcp_services:
            parts.append(
                "【可用 MCP 服务】\n"
                + "、".join(mcp_services)
                + "。需要这些服务中的实时或结构化数据时，应优先调用对应 MCP 工具。"
            )
        if local_request:
            parts.append("本轮已识别为本地文件任务，不得启动 web_research。")
        skill_ids = loads(agent.skills_json, [])
        skill_query = select(Skill).where(Skill.enabled.is_(True))
        if skill_ids:
            skill_query = skill_query.where(Skill.id.in_(skill_ids))
        skills = list((await db.scalars(skill_query.order_by(Skill.name))).all())
        if math_query and not any(skill.name == "jsxgraph-math-visualization" for skill in skills):
            math_skill = await db.scalar(
                select(Skill).where(
                    Skill.enabled.is_(True),
                    Skill.name == "jsxgraph-math-visualization",
                )
            )
            if math_skill:
                skills.append(math_skill)
        if skills:
            parts.append(
                "【已启用 Skills】\n"
                + "\n\n".join(f"## {skill.name}\n{skill.instructions}" for skill in skills)
            )
        if image_generation_available:
            parts.append(
                "【图片生成能力】\n"
                "系统已配置图片生成模型。只有当位图能显著帮助用户理解或用户明确要求图片时，"
                "才在回答末尾追加一个 ```image-prompt 代码块，块内仅写一段具体、完整的中文"
                "绘图提示词；系统会移除该代码块并调用图片 API。普通文字问题不要请求图片。"
                "数学函数和几何关系优先使用 JSXGraph 交互图，不要用图片替代精确数学图表。"
            )
        if rag_result and rag_result.get("enabled"):
            rendered_rag_prompt = self._render_rag_prompt(
                agent,
                query,
                rag_result,
                conversation_messages,
            )
            if rag_result.get("context") and "【知识库检索结果】" not in rendered_rag_prompt:
                rendered_rag_prompt += "\n\n【知识库检索结果】\n" + str(rag_result["context"])
            parts.append(rendered_rag_prompt)
        return "\n\n".join(parts)

    async def _execute_tool(
        self,
        db: AsyncSession,
        agent: AgentDefinition,
        run: AgentRun,
        name: str,
        arguments: dict[str, Any],
        execution: ExecutionContext,
        security_context: RuntimeSecurityContext,
        mcp_bindings: dict[str, tuple[Extension, str]],
    ) -> dict[str, Any]:
        if name == "call_agent":
            target = await db.scalar(
                select(AgentDefinition).where(
                    AgentDefinition.slug == str(arguments.get("agent_slug", "")),
                    AgentDefinition.status == "active",
                )
            )
            if not target:
                return {"status": "failed", "error": "目标 Agent 不存在"}
            if target.id == agent.id or target.id in execution.agent_stack:
                return {
                    "status": "failed",
                    "error": f"已阻止 Agent 循环调用: {agent.slug} -> {target.slug}",
                }
            child = await self.run(
                db,
                target.id,
                str(arguments.get("input", "")),
                execution=ExecutionContext(
                    depth=execution.depth + 1,
                    agent_stack=execution.agent_stack + [agent.id],
                    parent_run_id=run.id,
                    user_id=execution.user_id,
                    reply_style_prompt=execution.reply_style_prompt,
                    approval_run_id=execution.approval_run_id,
                    permission_mode=execution.permission_mode,
                    approval_policy_id=execution.approval_policy_id,
                    tool_policy=execution.tool_policy,
                ),
                user_context={
                    "security_profile": security_context.profile,
                    "user_id": execution.user_id,
                    "reply_style_prompt": execution.reply_style_prompt,
                },
                tool_policy=execution.tool_policy,
            )
            return {
                "status": child.status,
                "agent": target.slug,
                "run_id": child.id,
                "output": child.output_text,
                "error": child.error,
            }
        if name in mcp_bindings:
            extension, remote_name = mcp_bindings[name]
            result = await extension_service.call_mcp_tool(
                extension,
                remote_name,
                arguments,
                db=db,
                security_context=security_context,
            )
            await audit(
                db,
                "mcp.tool_executed",
                "extension",
                extension.id,
                {"agent_id": agent.id, "tool": remote_name},
            )
            return {"status": "completed", "mcp": extension.name, "result": result}
        permissions = loads(agent.permissions_json, {})
        mode = str(execution.permission_mode or permissions.get("tool_mode", "ask"))
        return await tool_runtime.execute(
            db,
            name,
            arguments,
            run_id=execution.approval_run_id or run.id,
            agent_id=agent.id,
            # A run-level permission mode is an explicit user choice and must not
            # be silently replaced by the Agent's saved approval policy.
            policy_id=(
                None
                if execution.permission_mode
                else execution.approval_policy_id or permissions.get("approval_policy_id")
            ),
            permission_mode=mode,
            security_context=security_context,
        )

    async def _publish_tool_result(
        self,
        db: AsyncSession,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        tool: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        await emit(
            {
                "type": "tool_result",
                "tool": tool,
                "status": result.get("status", "completed"),
                "error": result.get("error") or result.get("message"),
                "approval_id": result.get("approval_id"),
            }
        )
        if result.get("status") != "approval_required":
            return result
        approval_id = str(result["approval_id"])
        await emit(
            {
                "type": "approval_required",
                "approval_id": approval_id,
                "tool": tool,
                "risk": result.get("risk"),
                "message": result.get("message"),
            }
        )
        resolved = await tool_runtime.wait_for_approval(approval_id)
        await emit(
            {
                "type": "approval_resolved",
                "approval_id": approval_id,
                "tool": tool,
                "status": resolved.get("status"),
            }
        )
        return resolved


agent_engine = AgentEngine()
