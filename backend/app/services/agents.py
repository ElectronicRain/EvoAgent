from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..models import (
    Approval,
    AgentArtifact,
    AgentDefinition,
    AgentRun,
    Extension,
    ModelEndpoint,
    Skill,
)
from .common import audit, dumps, loads
from .extensions import extension_service
from .intent import TaskIntent, intent_service
from .knowledge import knowledge_service
from .llm import get_provider, provider_from_endpoint
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


class AgentEngine:
    research_role = re.compile(
        r"search|researcher|scholar|检索|搜索|查找|调查|调研|证据研究|资料搜集",
        re.I,
    )

    def is_research_agent(self, agent: AgentDefinition) -> bool:
        profile = " ".join(
            [agent.slug, agent.name, agent.description or "", agent.system_prompt or ""]
        )
        return bool(self.research_role.search(profile))

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

    async def _mcp_catalog(
        self, db: AsyncSession, permissions: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[Extension, str]], list[str], list[str]]:
        extensions = (
            await db.scalars(
                select(Extension).where(
                    Extension.kind == "mcp", Extension.enabled.is_(True)
                )
            )
        ).all()
        selected = permissions.get("mcp_extensions")
        if isinstance(selected, list):
            selected_ids = {str(item) for item in selected}
            extensions = [item for item in extensions if item.id in selected_ids]
        else:
            # Legacy Agents inherit the two local built-in MCP services. Custom remote
            # services are opt-in so a stale endpoint cannot delay every conversation.
            extensions = [
                item for item in extensions if extension_service._builtin_endpoint(item)
            ]
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
    ) -> AgentRun:
        execution = execution or ExecutionContext()
        if user_context:
            execution.user_id = str(user_context.get("user_id") or execution.user_id or "") or None
            execution.reply_style_prompt = str(
                user_context.get("reply_style_prompt")
                or execution.reply_style_prompt
                or ""
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
        security_profile = str(
            (user_context or {}).get("security_profile")
            or agent_permissions.get("security_profile")
            or "default"
        )
        security_context = await runtime_security_service.resolve(db, security_profile)
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
            intent = intent_service.classify(input_text)
            local_request = intent.category in {
                "command_execution",
                "local_workspace_change",
                "local_file_access",
            }
            permissions = agent_permissions
            allowed_tools = set(loads(agent.tools_json, [])) | {"exec"}
            mcp_schemas, mcp_bindings, mcp_services, mcp_errors = await self._mcp_catalog(
                db, permissions
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
            )
            await emit({"type": "intent_detected", **intent.as_dict()})
            await emit(
                {
                    "type": "context_ready",
                    "knowledge_attached": "【知识库检索结果】" in system_prompt,
                    "history_messages": len(conversation_messages or []),
                    "capabilities": {
                        "exec": True,
                        "skills": "【已启用 Skills】" in system_prompt,
                        "mcp_services": mcp_services,
                    },
                }
            )
            schemas = [
                item
                for item in tool_runtime.schemas()
                if item["function"]["name"] in allowed_tools
            ]
            if "call_agent" in allowed_tools:
                schemas.append(self.call_agent_schema())
            schemas.extend(mcp_schemas)
            if mcp_errors:
                await emit({"type": "mcp_unavailable", "errors": mcp_errors})
            endpoint = (
                await db.get(ModelEndpoint, agent.model_endpoint_id)
                if agent.model_endpoint_id
                else None
            )
            if agent.model_endpoint_id and (not endpoint or not endpoint.enabled):
                raise RuntimeError("Agent 绑定的模型接口不存在或已停用")
            provider = provider_from_endpoint(endpoint) if endpoint else get_provider(agent.provider)
            model_name = endpoint.default_model if endpoint else agent.model
            total_tokens = 0
            final_content = ""
            research_requested = (
                not local_request
                and intent.category == "web_research"
                and "web_research" in allowed_tools
            )
            research_sources: list[dict[str, Any]] = []
            if research_requested:
                research_sources = await web_research_service.collect(input_text, emit)
                if research_sources:
                    system_prompt += (
                        "\n\n系统已经代表你完成了真实联网检索，以下资料来自本轮实时搜索。"
                        "必须基于这些来源回答并给出可点击链接；不得声称自己无法联网、网络访问受限，"
                        "也不得把模型训练记忆冒充本轮检索结果。\n\n"
                        + web_research_service.context(research_sources)
                    )
                else:
                    await emit(
                        {
                            "type": "web_research_empty",
                            "message": "联网检索未取得可用页面，将基于现有上下文继续并标记待核验项。",
                        }
                    )
                await emit(
                    {
                        "type": "research_synthesis_started",
                        "sources": len(research_sources),
                    }
                )

            local_plan = None if intent.needs_clarification else tool_runtime.plan_local_request(input_text)
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

            for iteration in range(settings.max_tool_iterations):
                response = await provider.chat(
                    messages,
                    model=model_name,
                    temperature=agent.temperature,
                    tools=schemas or None,
                )
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
                        # Return the failure as a tool message so the model can inspect
                        # the workspace, choose another path, or continue without it.
                        message = str(exc).strip() or f"{type(exc).__name__}：工具执行失败"
                        result = {
                            "status": "failed",
                            "error": message,
                            "tool": item["name"],
                            "recovery": "请检查参数；读取文件前先调用 list_directory，或跳过该文件继续任务。",
                        }
                    result = await self._publish_tool_result(db, emit, item["name"], result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": item["id"],
                            "content": dumps(result),
                        }
                    )
            else:
                raise RuntimeError("Agent 工具调用次数超过限制")

            if research_requested and final_content:
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
                    )
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
                artifact_dir = settings.workspace_root / "artifacts"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                relative_path = f"artifacts/{agent.slug}-{run.id[:8]}.md"
                (settings.workspace_root / relative_path).write_text(markdown, encoding="utf-8")
                artifact = AgentArtifact(
                    run_id=run.id,
                    conversation_id=(user_context or {}).get("conversation_id"),
                    kind="markdown",
                    title=f"{title}.md",
                    relative_path=relative_path,
                    content=markdown,
                )
                db.add(artifact)
                await db.flush()
                final_content = markdown
                await emit(
                    {
                        "type": "artifact_created",
                        "artifact_id": artifact.id,
                        "title": artifact.title,
                        "path": artifact.relative_path,
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
    ) -> str:
        parts = [
            agent.system_prompt,
            "你运行在 EvoAgent 中。关键结论必须说明依据；不确定时明确标注。",
            "所有输出均为 AI 生成内容，涉及高风险领域必须建议人工复核。",
            (
                "当用户提到桌面、本地路径、文件或目录时，必须优先使用 list_directory、read_file、"
                "search_files 或 exec，禁止把本地请求改成网页搜索。不要猜测文件名；需要读取"
                "文件时先确认实际路径。路径可以是绝对路径，但必须符合本轮安全配置。"
            ),
            (
                f"本轮安全配置：文件系统模式 {security.filesystem_mode}；命令模式 "
                f"{security.command_mode}；授权根目录：{'；'.join(security.roots)}。"
            ),
            (
                "你已具备 exec 命令执行能力，但必须遵循本轮安全范围和审批方式。"
                "需要操作项目、运行测试或检查环境时，应主动使用工具并依据真实结果回答。"
            ),
        ]
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
        skills = (await db.scalars(skill_query.order_by(Skill.name))).all()
        if skills:
            parts.append(
                "【已启用 Skills】\n"
                + "\n\n".join(f"## {skill.name}\n{skill.instructions}" for skill in skills)
            )
        knowledge_base_ids = loads(agent.knowledge_bases_json, [])
        if knowledge_base_ids:
            results = await knowledge_service.search(db, query, knowledge_base_ids, top_k=5)
            if results:
                parts.append(
                    "【知识库检索结果】\n"
                    + "\n\n".join(
                        f"[{index}] {item['content']}\n来源：{item['citation']}"
                        for index, item in enumerate(results, 1)
                    )
                )
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
                ),
                user_context={
                    "security_profile": security_context.profile,
                    "user_id": execution.user_id,
                    "reply_style_prompt": execution.reply_style_prompt,
                },
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
        mode = str(permissions.get("tool_mode", "ask"))
        return await tool_runtime.execute(
            db,
            name,
            arguments,
            run_id=run.id,
            agent_id=agent.id,
            policy_id=permissions.get("approval_policy_id"),
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
        resolved = await self._wait_for_approval(approval_id)
        await emit(
            {
                "type": "approval_resolved",
                "approval_id": approval_id,
                "tool": tool,
                "status": resolved.get("status"),
            }
        )
        return resolved

    @staticmethod
    async def _wait_for_approval(approval_id: str) -> dict[str, Any]:
        for _attempt in range(1200):
            async with SessionLocal() as session:
                approval = await session.get(Approval, approval_id)
                if approval and approval.status != "pending":
                    if approval.status == "approved":
                        result = loads(approval.execution_result_json, {})
                        return result or {"status": "completed", "message": "操作已批准"}
                    return {"status": "denied", "message": "用户拒绝了该操作"}
            await asyncio.sleep(0.5)
        return {"status": "denied", "message": "等待用户审批超时"}


agent_engine = AgentEngine()
