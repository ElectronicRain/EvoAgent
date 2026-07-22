from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentDefinition, EvaluationCase, EvolutionProposal, Skill
from .agents import agent_engine
from .common import audit, dumps, loads
from .web_research import web_research_service


class EvolutionService:
    async def _prepare_assets(
        self,
        db: AsyncSession,
        proposal: EvolutionProposal,
        source: AgentDefinition,
        candidate: AgentDefinition,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        research_task = (
            f"为 Agent 的目标任务搜索可操作的改进方法与最佳实践。Agent：{source.name}；"
            f"目标：{source.description or proposal.reason}；本次进化目标：{proposal.reason}。"
            "重点检索任务提示词优化、可靠执行流程、结果质量评估、错误恢复和可追溯交付方法。"
        )
        await emit(
            {
                "type": "evolution_stage_started",
                "stage": "method_research",
                "label": "联网检索进化方法",
                "query": research_task,
            }
        )

        async def research_event(event: dict[str, Any]) -> None:
            await emit({"type": "evolution_research_event", "event": event})

        research_error = ""
        try:
            sources = await web_research_service.collect(research_task, research_event)
        except Exception as exc:
            research_error = str(exc).strip() or f"{type(exc).__name__}：联网检索失败"
            sources = []
            await emit(
                {
                    "type": "evolution_stage_warning",
                    "stage": "method_research",
                    "message": research_error,
                }
            )
        source_summaries = [
            {
                "title": item.get("title", "未命名来源"),
                "url": item.get("url", ""),
                "source": item.get("source", "Web"),
                "credibility": item.get("credibility") or {},
                "method_excerpt": " ".join(
                    str(item.get("content") or item.get("description") or "").split()
                )[:700],
            }
            for item in sources[:8]
        ]
        await emit(
            {
                "type": "evolution_methods_ready",
                "count": len(source_summaries),
                "sources": source_summaries,
                "research_error": research_error,
            }
        )

        existing_changes = loads(proposal.changes_json, {})
        original_prompt = str(
            existing_changes.get("original_prompt")
            or existing_changes.get("system_prompt")
            or candidate.system_prompt
        )
        optimized_task_prompt = (
            "请完成以下目标任务：\n{{task}}\n\n"
            "执行要求：先确认目标、约束与验收标准；再制定可检查的步骤并逐步执行；"
            "需要外部事实时必须检索并保留来源；工具失败时先诊断和替代，不因单点失败终止；"
            "交付前按完整性、事实依据、可追溯性与风险边界自检，最后给出成果和待人工核验项。"
        )
        optimized_system_prompt = (
            f"{original_prompt.strip()}\n\n"
            "【进化后的目标任务执行协议】\n"
            "1. 将用户目标改写为明确的交付物、约束条件和可验证验收标准。\n"
            "2. 对复杂任务先规划，再执行检索/工具调用，并记录关键中间结论。\n"
            "3. 读取本地文件前先确认真实路径；单个来源或工具失败时采用替代方案继续。\n"
            "4. 区分事实、推断与建议；外部事实附可点击来源及可信度。\n"
            "5. 交付前执行反例检查、遗漏检查和目标一致性检查。\n\n"
            f"【默认目标任务模板】\n{optimized_task_prompt}"
        )
        candidate.system_prompt = optimized_system_prompt
        await emit(
            {
                "type": "evolution_prompt_optimized",
                "original_prompt": original_prompt,
                "optimized_prompt": optimized_system_prompt,
                "task_prompt_template": optimized_task_prompt,
            }
        )

        method_lines = []
        for index, item in enumerate(source_summaries, 1):
            credibility = item.get("credibility") or {}
            method_lines.append(
                f"{index}. **{item['title']}**（可信度 {credibility.get('score', 0)}/100）\n"
                f"   - 方法依据：{item['method_excerpt'] or '仅取得题录信息，使用前需核验原文。'}\n"
                f"   - 来源：{item['url']}"
            )
        skill_name = f"{source.name} 自我进化方法 v{candidate.version}"
        skill_description = f"由 EvoAgent 围绕“{proposal.reason}”联网检索并封装的候选版本专属 Skill。"
        skill_instructions = (
            "# 目标\n\n"
            f"帮助 {source.name} 在“{source.description or proposal.reason}”任务中提高可靠性、"
            "目标一致性、错误恢复能力与成果可追溯性。\n\n"
            "# 使用流程\n\n"
            "1. 将输入转化为交付物、约束和验收标准。\n"
            "2. 给出分步计划，并在每一步标明输入、操作、输出与失败替代方案。\n"
            "3. 需要事实时联网检索；学术任务优先学术来源，其他任务优先官网和权威网页。\n"
            "4. 工具或文件失败时检查参数和真实路径，记录失败后继续可执行部分。\n"
            "5. 输出前检查完整性、证据、引用、风险边界和目标任务一致性。\n\n"
            "# 联网整理的方法依据\n\n"
            + ("\n\n".join(method_lines) if method_lines else "本轮未取得可用网页来源；仅启用通用可靠执行协议，使用前需人工复核。")
            + "\n\n# 目标任务提示词模板\n\n"
            + optimized_task_prompt
        )
        skill = await db.scalar(select(Skill).where(Skill.name == skill_name))
        if not skill:
            skill = Skill(name=skill_name, instructions=skill_instructions)
            db.add(skill)
            await db.flush()
        skill.description = skill_description
        skill.instructions = skill_instructions
        skill.version = f"{candidate.version}.0.0"
        skill.enabled = True
        skill_dir = settings.skills_root / f"evolution-{candidate.slug}"
        skill_path = skill_dir / "SKILL.md"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = (
            "---\n"
            f"name: {dumps(skill_name)}\n"
            f"description: {dumps(skill_description)}\n"
            f"version: {dumps(skill.version)}\n"
            "---\n\n"
            f"{skill_instructions}\n"
        )
        skill_path.write_text(skill_file, encoding="utf-8")
        skill.source_path = str(skill_path)
        candidate.skills_json = dumps(
            list(dict.fromkeys([*loads(candidate.skills_json, []), skill.id]))
        )
        changes = loads(proposal.changes_json, {})
        changes.update(
            {
                "original_prompt": original_prompt,
                "optimized_prompt": optimized_system_prompt,
                "task_prompt_template": optimized_task_prompt,
                "generated_skill_id": skill.id,
                "generated_skill_name": skill.name,
                "generated_skill_path": str(skill_path),
                "research_source_count": len(source_summaries),
            }
        )
        proposal.changes_json = dumps(changes)
        await db.flush()
        await emit(
            {
                "type": "evolution_skill_packaged",
                "skill": {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "version": skill.version,
                    "instructions": skill.instructions,
                    "source_path": skill.source_path,
                },
            }
        )
        return {
            "research_query": research_task,
            "research_sources": source_summaries,
            "research_error": research_error,
            "skill": {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
                "instructions": skill.instructions,
                "source_path": skill.source_path,
            },
            "original_prompt": original_prompt,
            "optimized_prompt": optimized_system_prompt,
            "task_prompt_template": optimized_task_prompt,
        }

    async def create_proposal(
        self,
        db: AsyncSession,
        source: AgentDefinition,
        reason: str,
        proposed_prompt: str,
        proposed_tools: list[str] | None,
    ) -> EvolutionProposal:
        candidate = AgentDefinition(
            lineage_id=source.lineage_id,
            parent_id=source.id,
            model_endpoint_id=source.model_endpoint_id,
            name=source.name,
            slug=f"{source.slug}-v{source.version + 1}",
            description=source.description,
            system_prompt=proposed_prompt,
            provider=source.provider,
            model=source.model,
            temperature=source.temperature,
            tools_json=dumps(proposed_tools if proposed_tools is not None else loads(source.tools_json, [])),
            skills_json=source.skills_json,
            knowledge_bases_json=source.knowledge_bases_json,
            permissions_json=source.permissions_json,
            version=source.version + 1,
            status="candidate",
        )
        db.add(candidate)
        await db.flush()
        proposal = EvolutionProposal(
            source_agent_id=source.id,
            candidate_agent_id=candidate.id,
            reason=reason,
            changes_json=dumps(
                {
                    "system_prompt": proposed_prompt,
                    "tools": proposed_tools,
                    "safety": "候选版本必须通过评测和人工审批后才能激活",
                }
            ),
            status="draft",
        )
        db.add(proposal)
        await db.flush()
        await audit(db, "evolution.proposed", "evolution_proposal", proposal.id)
        return proposal

    async def evaluate(
        self,
        db: AsyncSession,
        proposal: EvolutionProposal,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> EvolutionProposal:
        async def emit(event: dict[str, Any]) -> None:
            if on_event:
                await on_event(event)

        source = await db.get(AgentDefinition, proposal.source_agent_id)
        candidate = await db.get(AgentDefinition, proposal.candidate_agent_id)
        if not source or not candidate:
            raise LookupError("进化版本不存在")
        if proposal.status in {"approved", "rejected"}:
            raise ValueError("已完成审批的提案不能重新评测")
        cases = (await db.scalars(select(EvaluationCase).order_by(EvaluationCase.created_at))).all()
        if not cases:
            raise ValueError("至少需要一个评测用例")
        proposal.status = "evaluating"
        proposal.report_json = dumps(
            {"cases": [], "count": len(cases), "completed": 0, "status": "evaluating"}
        )
        await db.flush()
        await emit({"type": "evaluation_started", "total_cases": len(cases)})
        assets = await self._prepare_assets(db, proposal, source, candidate, emit)
        proposal.report_json = dumps(
            {
                "cases": [],
                "count": len(cases),
                "completed": 0,
                "status": "evaluating",
                **assets,
            }
        )
        await db.flush()
        await emit(
            {
                "type": "evolution_stage_started",
                "stage": "benchmark",
                "label": "运行新旧版本对照评测",
            }
        )
        report: list[dict[str, Any]] = []
        source_scores: list[float] = []
        candidate_scores: list[float] = []
        for index, case in enumerate(cases, 1):
            await emit(
                {
                    "type": "evaluation_case_started",
                    "index": index,
                    "total_cases": len(cases),
                    "case_id": case.id,
                    "case": case.name,
                }
            )

            async def baseline_event(event: dict[str, Any]) -> None:
                await emit(
                    {
                        "type": "evaluation_agent_event",
                        "phase": "baseline",
                        "case": case.name,
                        "event": event,
                    }
                )

            await emit({"type": "evaluation_phase_started", "phase": "baseline", "case": case.name})
            baseline = await agent_engine.run(
                db, source.id, case.input_text, on_event=baseline_event
            )

            async def candidate_event(event: dict[str, Any]) -> None:
                await emit(
                    {
                        "type": "evaluation_agent_event",
                        "phase": "candidate",
                        "case": case.name,
                        "event": event,
                    }
                )

            await emit({"type": "evaluation_phase_started", "phase": "candidate", "case": case.name})
            evolved = await agent_engine.run(
                db, candidate.id, case.input_text, on_event=candidate_event
            )
            keywords = loads(case.expected_keywords_json, [])
            baseline_score = (
                self._score(baseline.output_text, keywords, case.requires_citation)
                if baseline.status == "completed"
                else 0.0
            )
            candidate_score = (
                self._score(evolved.output_text, keywords, case.requires_citation)
                if evolved.status == "completed"
                else 0.0
            )
            source_scores.append(baseline_score)
            candidate_scores.append(candidate_score)
            case_result = {
                "case": case.name,
                "baseline": baseline_score,
                "candidate": candidate_score,
                "delta": round(candidate_score - baseline_score, 2),
                "baseline_status": baseline.status,
                "candidate_status": evolved.status,
                "baseline_error": baseline.error,
                "candidate_error": evolved.error,
                "baseline_run_id": baseline.id,
                "candidate_run_id": evolved.id,
            }
            report.append(case_result)
            proposal.report_json = dumps(
                {
                    "cases": report,
                    "count": len(cases),
                    "completed": len(report),
                    "status": "evaluating",
                    **assets,
                }
            )
            await db.flush()
            await emit(
                {
                    "type": "evaluation_case_completed",
                    "index": index,
                    "total_cases": len(cases),
                    **case_result,
                }
            )
        proposal.baseline_score = round(sum(source_scores) / len(source_scores), 2)
        proposal.candidate_score = round(sum(candidate_scores) / len(candidate_scores), 2)
        artifact_dir = settings.workspace_root / "evolution"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        relative_path = f"evolution/{candidate.slug}-evaluation.md"
        source_markdown = "\n".join(
            f"{index}. [{item['title']}]({item['url']}) · {item['source']} · "
            f"可信度 {(item.get('credibility') or {}).get('score', 0)}/100"
            for index, item in enumerate(assets["research_sources"], 1)
        )
        cases_markdown = "\n".join(
            f"| {item['case']} | {item['baseline']} | {item['candidate']} | {item['delta']} |"
            for item in report
        )
        artifact_content = (
            f"# {source.name} 进化成果 v{candidate.version}\n\n"
            f"> 进化目标：{proposal.reason}\n\n"
            "## 进化成果摘要\n\n"
            f"- 基线平均分：{proposal.baseline_score}\n"
            f"- 候选平均分：{proposal.candidate_score}\n"
            f"- 生成 Skill：{assets['skill']['name']}\n"
            f"- 联网方法来源：{len(assets['research_sources'])} 条\n\n"
            "## 优化前系统提示词\n\n"
            f"{assets['original_prompt']}\n\n"
            "## 优化后系统提示词\n\n"
            f"{assets['optimized_prompt']}\n\n"
            "## 优化后的目标任务提示词模板\n\n"
            f"{assets['task_prompt_template']}\n\n"
            "## 封装的 Skill\n\n"
            f"{assets['skill']['instructions']}\n\n"
            "## 联网来源\n\n"
            f"{source_markdown or '本轮没有取得可用来源，相关方法需人工复核。'}\n\n"
            "## 新旧版本评测\n\n"
            "| 用例 | 基线 | 候选 | 变化 |\n|---|---:|---:|---:|\n"
            f"{cases_markdown}\n"
        )
        (settings.workspace_root / relative_path).write_text(
            artifact_content, encoding="utf-8"
        )
        artifact = {
            "title": f"{source.name} 进化成果 v{candidate.version}.md",
            "relative_path": relative_path,
            "content": artifact_content,
        }
        proposal.report_json = dumps(
            {
                "cases": report,
                "count": len(report),
                "completed": len(report),
                "status": "completed",
                **assets,
                "artifact": artifact,
            }
        )
        proposal.status = "evaluated"
        await audit(
            db,
            "evolution.evaluated",
            "evolution_proposal",
            proposal.id,
            {"baseline": proposal.baseline_score, "candidate": proposal.candidate_score},
        )
        await emit(
            {
                "type": "evolution_artifact_created",
                "artifact": artifact,
            }
        )
        await emit(
            {
                "type": "evaluation_completed",
                "baseline_score": proposal.baseline_score,
                "candidate_score": proposal.candidate_score,
                "total_cases": len(cases),
            }
        )
        return proposal

    def _score(self, output: str, keywords: list[str], requires_citation: bool) -> float:
        keyword_score = (
            sum(1 for keyword in keywords if keyword.lower() in output.lower()) / len(keywords) * 80
            if keywords
            else 60
        )
        citation_score = 20 if not requires_citation or any(
            token in output for token in ("引用", "来源", "依据", "[1]")
        ) else 0
        return round(min(100, keyword_score + citation_score), 2)

    async def decide(
        self, db: AsyncSession, proposal: EvolutionProposal, approved: bool, actor: str
    ) -> EvolutionProposal:
        source = await db.get(AgentDefinition, proposal.source_agent_id)
        candidate = await db.get(AgentDefinition, proposal.candidate_agent_id)
        if not source or not candidate:
            raise LookupError("Agent 版本不存在")
        if approved:
            if proposal.status != "evaluated":
                raise ValueError("候选版本必须先完成评测")
            source.status = "archived"
            candidate.status = "active"
            proposal.status = "approved"
        else:
            candidate.status = "rejected"
            proposal.status = "rejected"
        await audit(
            db,
            "evolution.decided",
            "evolution_proposal",
            proposal.id,
            {"approved": approved, "actor": actor},
            actor=actor,
        )
        return proposal


evolution_service = EvolutionService()
