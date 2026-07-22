from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentArtifact, AgentDefinition, AgentRun, ModelEndpoint, Skill
from .common import audit, dumps, loads
from .knowledge import knowledge_service
from .llm import get_provider, provider_from_endpoint
from .tools import tool_runtime
from .web_research import web_research_service


@dataclass
class ExecutionContext:
    depth: int = 0
    agent_stack: list[str] = field(default_factory=list)
    parent_run_id: str | None = None


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
        if execution.depth >= settings.max_agent_depth:
            raise RuntimeError("已达到 Agent 最大调用深度")
        agent = await db.get(AgentDefinition, agent_id)
        if not agent or agent.status not in {"active", "candidate"}:
            raise LookupError("Agent 不存在或未启用")
        if agent.id in execution.agent_stack:
            chain = " -> ".join(execution.agent_stack + [agent.id])
            raise RuntimeError(f"检测到 Agent 循环调用: {chain}")

        run = AgentRun(
            agent_id=agent.id,
            parent_run_id=execution.parent_run_id,
            status="running",
            input_text=input_text,
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
                }
            )
            system_prompt = await self._build_system_prompt(db, agent, input_text)
            await emit(
                {
                    "type": "context_ready",
                    "knowledge_attached": "【知识库检索结果】" in system_prompt,
                    "history_messages": len(conversation_messages or []),
                }
            )
            allowed_tools = set(loads(agent.tools_json, []))
            schemas = [
                item
                for item in tool_runtime.schemas()
                if item["function"]["name"] in allowed_tools
            ]
            if "call_agent" in allowed_tools:
                schemas.append(self.call_agent_schema())
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
            research_requested = web_research_service.should_research(input_text) or (
                "web_research" in allowed_tools and self.is_research_agent(agent)
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
                    await emit(
                        {
                            "type": "tool_result",
                            "tool": item["name"],
                            "status": result.get("status", "completed"),
                            "error": result.get("error"),
                        }
                    )
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
        self, db: AsyncSession, agent: AgentDefinition, query: str
    ) -> str:
        parts = [
            agent.system_prompt,
            "你运行在 EvoAgent 中。关键结论必须说明依据；不确定时明确标注。",
            "所有输出均为 AI 生成内容，涉及高风险领域必须建议人工复核。",
            (
                "使用本地文件工具时，路径必须相对于工作区。不要猜测文件名；需要读取文件时先用 "
                "list_directory 或 search_files 确认可用路径。若某个文件不存在，应改用实际存在的文件，"
                "或说明缺失并继续完成其余任务，不得因此终止整个任务。"
            ),
        ]
        skill_ids = loads(agent.skills_json, [])
        if skill_ids:
            skills = (
                await db.scalars(select(Skill).where(Skill.id.in_(skill_ids), Skill.enabled.is_(True)))
            ).all()
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
                ),
            )
            return {
                "status": child.status,
                "agent": target.slug,
                "run_id": child.id,
                "output": child.output_text,
                "error": child.error,
            }
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
        )


agent_engine = AgentEngine()
