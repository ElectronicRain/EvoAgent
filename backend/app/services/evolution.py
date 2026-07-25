from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentDefinition, AgentRun, EvaluationCase, EvolutionProposal, Skill
from .agents import agent_engine
from .common import audit, dumps, loads
from .web_research import web_research_service


class EvolutionService:
    GOAL_DIMENSIONS = {
        "evidence": {
            "label": "证据与引用",
            "keywords": ("证据", "引用", "来源", "可信", "事实", "学术"),
            "criteria": ["区分事实与推断", "关键结论附可核验来源", "标注不确定性"],
        },
        "structure": {
            "label": "结构与完整性",
            "keywords": ("结构", "完整", "遗漏", "清晰", "分步", "五点", "覆盖"),
            "criteria": ["完整覆盖用户要求", "先结论后依据", "交付结构可扫描"],
        },
        "tool_use": {
            "label": "工具执行",
            "keywords": ("工具", "exec", "命令", "mcp", "skill", "文件", "本地"),
            "criteria": ["优先使用真实工具", "失败后诊断并替代", "不伪造执行结果"],
        },
        "reliability": {
            "label": "稳定性与恢复",
            "keywords": ("稳定", "失败", "恢复", "超时", "可靠", "错误"),
            "criteria": ["单点失败不中止整体任务", "保留中间结果", "明确失败边界"],
        },
        "intent": {
            "label": "意图理解",
            "keywords": ("意图", "理解", "目标", "智能", "自主", "澄清"),
            "criteria": ["识别真实交付目标", "补全隐含约束", "减少不必要追问"],
        },
        "safety": {
            "label": "安全与边界",
            "keywords": ("安全", "审批", "权限", "风险", "隐私", "伦理"),
            "criteria": ["高风险动作遵守安全策略", "权限不自行扩大", "风险清晰可见"],
        },
    }

    async def analyze_goal(
        self,
        db: AsyncSession,
        source: AgentDefinition,
        goal: str,
        include_run_insights: bool = True,
    ) -> dict[str, Any]:
        runs: list[AgentRun] = []
        if include_run_insights:
            runs = list(
                (
                    await db.scalars(
                        select(AgentRun)
                        .where(AgentRun.agent_id == source.id)
                        .order_by(desc(AgentRun.created_at))
                        .limit(50)
                    )
                ).all()
            )
        completed = [item for item in runs if item.status == "completed"]
        failed = [item for item in runs if item.status != "completed"]
        trace_types: Counter[str] = Counter()
        failure_messages: Counter[str] = Counter()
        for run in runs:
            for event in loads(run.trace_json, []):
                trace_types[str(event.get("type") or "unknown")] += 1
                if event.get("status") == "failed" or event.get("type") in {
                    "tool_error",
                    "mcp_unavailable",
                    "knowledge_archive_failed",
                }:
                    message = str(
                        event.get("error")
                        or event.get("message")
                        or event.get("type")
                    ).strip()
                    if message:
                        failure_messages[message[:160]] += 1
            if run.error:
                failure_messages[run.error[:160]] += 1

        normalized_goal = " ".join(goal.split())
        selected_dimensions = [
            key
            for key, definition in self.GOAL_DIMENSIONS.items()
            if any(keyword.lower() in normalized_goal.lower() for keyword in definition["keywords"])
        ]
        if not selected_dimensions:
            selected_dimensions = ["intent", "structure", "reliability"]
        elif "intent" not in selected_dimensions:
            selected_dimensions.insert(0, "intent")
        selected_dimensions = selected_dimensions[:4]

        criteria: list[str] = []
        for key in selected_dimensions:
            criteria.extend(self.GOAL_DIMENSIONS[key]["criteria"])
        criteria = list(dict.fromkeys(criteria))[:8]
        observations = []
        if runs:
            success_rate = round(len(completed) * 100 / len(runs), 1)
            observations.append(f"最近 {len(runs)} 次运行成功率为 {success_rate}%")
            observations.append(
                f"平均耗时 {round(sum(item.duration_ms for item in runs) / len(runs))} ms，"
                f"平均 Token {round(sum(item.token_usage for item in runs) / len(runs))}"
            )
            if failure_messages:
                observations.append(
                    f"高频失败信号：{failure_messages.most_common(1)[0][0]}"
                )
        else:
            success_rate = None
            observations.append("暂无可分析的历史运行，将使用目标驱动的基准进化")

        recent_inputs = [
            " ".join(item.input_text.split())[:240]
            for item in failed[:2]
            if item.input_text.strip()
        ]
        prompt_sections = [
            source.system_prompt.strip(),
            "",
            "【本轮进化目标】",
            normalized_goal,
            "",
            "【必须满足的成功标准】",
            *[f"{index}. {criterion}" for index, criterion in enumerate(criteria, 1)],
            "",
            "【目标执行协议】",
            "1. 先识别用户真正需要的交付物、范围、约束和验收标准，再决定是否需要澄清。",
            "2. 对多项要求建立覆盖清单，执行完成后逐项核对，避免只返回前半部分。",
            "3. 需要事实、文件或系统状态时调用真实工具；不得把推测描述成已执行结果。",
            "4. 工具或来源失败时先诊断、尝试安全替代方案，并保留已经完成的成果。",
            "5. 输出前检查完整性、事实依据、风险边界和行动可执行性。",
        ]
        if failure_messages:
            prompt_sections.extend(
                [
                    "",
                    "【历史失败规避】",
                    *[
                        f"- {message}（出现 {count} 次）"
                        for message, count in failure_messages.most_common(3)
                    ],
                ]
            )
        suggested_cases = []
        for index, text in enumerate(recent_inputs, 1):
            suggested_cases.append(
                {
                    "name": f"历史失败复现 {index}",
                    "category": "reliability",
                    "discipline": "真实轨迹",
                    "input": text,
                    "expected_keywords": [],
                    "requires_citation": "evidence" in selected_dimensions,
                    "weight": 1.5,
                }
            )
        suggested_cases.append(
            {
                "name": "进化目标验收",
                "category": (
                    selected_dimensions[-1]
                    if selected_dimensions[-1]
                    in {"reliability", "evidence", "safety", "tool_use"}
                    else "quality"
                ),
                "discipline": "目标驱动",
                "input": f"请完成一个能够验证以下改进目标的真实任务，并明确展示验收结果：{normalized_goal}",
                "expected_keywords": [
                    word
                    for word in re.findall(r"[\u4e00-\u9fff]{2,6}", normalized_goal)
                    if word not in {"我需要", "希望", "完成", "优化"}
                ][:6],
                "requires_citation": "evidence" in selected_dimensions,
                "weight": 2,
            }
        )
        return {
            "agent_id": source.id,
            "agent_name": source.name,
            "goal": normalized_goal,
            "summary": f"围绕“{normalized_goal}”提升 {source.name} 的可验证任务完成能力。",
            "dimensions": [
                {
                    "id": key,
                    "label": self.GOAL_DIMENSIONS[key]["label"],
                }
                for key in selected_dimensions
            ],
            "success_criteria": criteria,
            "observations": observations,
            "run_insights": {
                "sample_size": len(runs),
                "success_rate": success_rate,
                "failed_runs": len(failed),
                "tool_failures": sum(
                    count
                    for event, count in trace_types.items()
                    if "error" in event or "failed" in event
                ),
                "top_failures": [
                    {"message": message, "count": count}
                    for message, count in failure_messages.most_common(5)
                ],
            },
            "recommended_prompt": "\n".join(prompt_sections).strip(),
            "suggested_cases": suggested_cases,
            "confidence": "high" if len(runs) >= 10 else "medium" if runs else "initial",
        }
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
        *,
        selected_case_ids: list[str] | None = None,
        min_candidate_score: float = 70,
        min_improvement: float = 0,
        max_failure_rate: float = 0.25,
        goal_analysis: dict[str, Any] | None = None,
    ) -> EvolutionProposal:
        analysis = goal_analysis or await self.analyze_goal(db, source, reason)
        proposed_prompt = proposed_prompt.strip() or str(
            analysis.get("recommended_prompt") or source.system_prompt
        )
        lineage_version = await db.scalar(
            select(func.max(AgentDefinition.version)).where(
                AgentDefinition.lineage_id == source.lineage_id
            )
        )
        next_version = max(source.version, int(lineage_version or 0)) + 1
        candidate = AgentDefinition(
            lineage_id=source.lineage_id,
            parent_id=source.id,
            model_endpoint_id=source.model_endpoint_id,
            name=source.name,
            slug=f"{source.slug.split('-v', 1)[0]}-v{next_version}",
            description=source.description,
            system_prompt=proposed_prompt,
            provider=source.provider,
            model=source.model,
            temperature=source.temperature,
            tools_json=dumps(proposed_tools if proposed_tools is not None else loads(source.tools_json, [])),
            skills_json=source.skills_json,
            knowledge_bases_json=source.knowledge_bases_json,
            permissions_json=source.permissions_json,
            version=next_version,
            status="candidate",
        )
        db.add(candidate)
        await db.flush()
        proposal = EvolutionProposal(
            source_agent_id=source.id,
            candidate_agent_id=candidate.id,
            reason=reason,
            goal_json=dumps(analysis),
            config_json=dumps(
                {
                    "selected_case_ids": selected_case_ids or [],
                    "min_candidate_score": min_candidate_score,
                    "min_improvement": min_improvement,
                    "max_failure_rate": max_failure_rate,
                }
            ),
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
        config = loads(proposal.config_json, {})
        selected_case_ids = [
            str(item) for item in config.get("selected_case_ids", []) if item
        ]
        case_query = select(EvaluationCase).where(EvaluationCase.enabled.is_(True))
        if selected_case_ids:
            case_query = case_query.where(EvaluationCase.id.in_(selected_case_ids))
        cases = (
            await db.scalars(
                case_query.order_by(EvaluationCase.created_at)
            )
        ).all()
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
        weights: list[float] = []
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
            baseline_details = (
                self._score_details(
                    baseline.output_text,
                    keywords,
                    case.requires_citation,
                    category=case.category,
                )
                if baseline.status == "completed"
                else self._empty_score()
            )
            candidate_details = (
                self._score_details(
                    evolved.output_text,
                    keywords,
                    case.requires_citation,
                    category=case.category,
                )
                if evolved.status == "completed"
                else self._empty_score()
            )
            baseline_score = baseline_details["total"]
            candidate_score = candidate_details["total"]
            source_scores.append(baseline_score)
            candidate_scores.append(candidate_score)
            weights.append(case.weight)
            case_result = {
                "case_id": case.id,
                "case": case.name,
                "category": case.category,
                "weight": case.weight,
                "baseline": baseline_score,
                "candidate": candidate_score,
                "delta": round(candidate_score - baseline_score, 2),
                "baseline_breakdown": baseline_details["breakdown"],
                "candidate_breakdown": candidate_details["breakdown"],
                "baseline_excerpt": baseline.output_text[:1000],
                "candidate_excerpt": evolved.output_text[:1000],
                "baseline_tokens": baseline.token_usage,
                "candidate_tokens": evolved.token_usage,
                "baseline_duration_ms": baseline.duration_ms,
                "candidate_duration_ms": evolved.duration_ms,
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
        weight_total = sum(weights) or 1
        proposal.baseline_score = round(
            sum(score * weight for score, weight in zip(source_scores, weights, strict=True))
            / weight_total,
            2,
        )
        proposal.candidate_score = round(
            sum(
                score * weight
                for score, weight in zip(candidate_scores, weights, strict=True)
            )
            / weight_total,
            2,
        )
        candidate_failures = sum(
            item["candidate_status"] != "completed" for item in report
        )
        failure_rate = round(candidate_failures / len(report), 4)
        score_delta = round(proposal.candidate_score - proposal.baseline_score, 2)
        gate_checks = [
            {
                "id": "candidate_score",
                "label": "候选质量达到发布线",
                "passed": proposal.candidate_score
                >= float(config.get("min_candidate_score", 70)),
                "actual": proposal.candidate_score,
                "target": float(config.get("min_candidate_score", 70)),
            },
            {
                "id": "improvement",
                "label": "相对基线无退化",
                "passed": score_delta >= float(config.get("min_improvement", 0)),
                "actual": score_delta,
                "target": float(config.get("min_improvement", 0)),
            },
            {
                "id": "failure_rate",
                "label": "运行失败率受控",
                "passed": failure_rate <= float(config.get("max_failure_rate", 0.25)),
                "actual": failure_rate,
                "target": float(config.get("max_failure_rate", 0.25)),
            },
        ]
        gate = {
            "passed": all(item["passed"] for item in gate_checks),
            "checks": gate_checks,
            "score_delta": score_delta,
            "failure_rate": failure_rate,
            "recommendation": (
                "建议批准并激活候选版本"
                if all(item["passed"] for item in gate_checks)
                else "建议继续优化或调整评测门槛，不要直接发布"
            ),
        }
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
            "## 发布门禁\n\n"
            f"- 结论：{'通过' if gate['passed'] else '未通过'}\n"
            f"- 分数变化：{gate['score_delta']}\n"
            f"- 候选失败率：{round(gate['failure_rate'] * 100, 1)}%\n"
            f"- 建议：{gate['recommendation']}\n\n"
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
                "gate": gate,
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

    def _empty_score(self) -> dict[str, Any]:
        return {
            "total": 0.0,
            "breakdown": {
                "coverage": 0,
                "evidence": 0,
                "structure": 0,
                "reliability": 0,
            },
        }

    def _score_details(
        self,
        output: str,
        keywords: list[str],
        requires_citation: bool,
        *,
        category: str = "quality",
    ) -> dict[str, Any]:
        normalized = output.lower()
        matched = [
            keyword for keyword in keywords if keyword.lower() in normalized
        ]
        coverage = (
            len(matched) / len(keywords) * 45
            if keywords
            else min(45, 25 + len(output) / 120)
        )
        evidence_tokens = ("引用", "来源", "依据", "http://", "https://", "[1]", "核验")
        has_evidence = any(token in normalized for token in evidence_tokens)
        evidence = 25 if has_evidence else (25 if not requires_citation else 0)
        structure_markers = (
            "\n1.",
            "\n- ",
            "## ",
            "结论",
            "步骤",
            "验收",
            "风险",
        )
        structure_hits = sum(marker in output for marker in structure_markers)
        structure = min(20, 7 + structure_hits * 3)
        reliability_tokens = ("风险", "失败", "替代", "核验", "不确定", "边界", "检查")
        reliability_hits = sum(token in output for token in reliability_tokens)
        reliability = min(10, 2 + reliability_hits * 2)
        if category == "evidence":
            evidence = min(30, evidence * 1.2)
        elif category == "reliability":
            reliability = min(15, reliability * 1.5)
        total = round(min(100, coverage + evidence + structure + reliability), 2)
        return {
            "total": total,
            "breakdown": {
                "coverage": round(coverage, 2),
                "evidence": round(evidence, 2),
                "structure": round(structure, 2),
                "reliability": round(reliability, 2),
            },
            "matched_keywords": matched,
            "missing_keywords": [item for item in keywords if item not in matched],
        }

    def _score(self, output: str, keywords: list[str], requires_citation: bool) -> float:
        return self._score_details(output, keywords, requires_citation)["total"]

    async def decide(
        self,
        db: AsyncSession,
        proposal: EvolutionProposal,
        approved: bool,
        actor: str,
        *,
        override_gate: bool = False,
        note: str = "",
    ) -> EvolutionProposal:
        source = await db.get(AgentDefinition, proposal.source_agent_id)
        candidate = await db.get(AgentDefinition, proposal.candidate_agent_id)
        if not source or not candidate:
            raise LookupError("Agent 版本不存在")
        if approved:
            if proposal.status != "evaluated":
                raise ValueError("候选版本必须先完成评测")
            gate = loads(proposal.report_json, {}).get("gate") or {}
            if gate and not gate.get("passed") and not override_gate:
                raise ValueError("候选版本未通过发布门禁；请继续优化，或填写原因后明确覆盖门禁")
            source.status = "archived"
            candidate.status = "active"
            proposal.status = "approved"
        else:
            candidate.status = "rejected"
            proposal.status = "rejected"
        proposal.decision_json = dumps(
            {
                "approved": approved,
                "actor": actor,
                "override_gate": override_gate,
                "note": note,
                "decided_at": datetime.now(timezone.utc),
            }
        )
        await audit(
            db,
            "evolution.decided",
            "evolution_proposal",
            proposal.id,
            {"approved": approved, "actor": actor},
            actor=actor,
        )
        return proposal

    async def rollback(
        self,
        db: AsyncSession,
        active: AgentDefinition,
        target: AgentDefinition,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        if active.lineage_id != target.lineage_id:
            raise ValueError("只能回滚到同一版本谱系中的 Agent")
        if active.id == target.id:
            raise ValueError("目标版本已经是当前激活版本")
        if active.status != "active":
            raise ValueError("只能从当前激活版本发起回滚")
        if target.status not in {"archived", "rejected"}:
            raise ValueError("目标版本不是可恢复的历史版本")
        active.status = "archived"
        target.status = "active"
        detail = {
            "from_agent_id": active.id,
            "from_version": active.version,
            "to_agent_id": target.id,
            "to_version": target.version,
            "reason": reason,
            "actor": actor,
        }
        await audit(
            db,
            "evolution.rolled_back",
            "agent",
            target.id,
            detail,
            actor=actor,
        )
        return detail

    async def overview(self, db: AsyncSession) -> dict[str, Any]:
        proposals = list(
            (
                await db.scalars(
                    select(EvolutionProposal).order_by(
                        desc(EvolutionProposal.created_at)
                    )
                )
            ).all()
        )
        evaluated = [
            item
            for item in proposals
            if item.status in {"evaluated", "approved", "rejected"}
            and loads(item.report_json, {}).get("status") == "completed"
        ]
        improvements = [
            item.candidate_score - item.baseline_score for item in evaluated
        ]
        gates_passed = sum(
            bool((loads(item.report_json, {}).get("gate") or {}).get("passed"))
            for item in evaluated
        )
        return {
            "summary": {
                "total": len(proposals),
                "draft": sum(item.status == "draft" for item in proposals),
                "evaluating": sum(item.status == "evaluating" for item in proposals),
                "evaluated": len(evaluated),
                "approved": sum(item.status == "approved" for item in proposals),
                "average_improvement": round(
                    sum(improvements) / len(improvements), 2
                )
                if improvements
                else 0,
                "gate_pass_rate": round(gates_passed * 100 / len(evaluated), 1)
                if evaluated
                else 0,
            },
            "pipeline": [
                {
                    "id": "discover",
                    "label": "目标诊断",
                    "description": "结合目标与历史轨迹识别进化重点",
                },
                {
                    "id": "candidate",
                    "label": "候选生成",
                    "description": "生成独立版本、提示词与专属 Skill",
                },
                {
                    "id": "benchmark",
                    "label": "对照评测",
                    "description": "基线与候选按多维评分运行相同用例",
                },
                {
                    "id": "gate",
                    "label": "发布门禁",
                    "description": "检查质量、提升幅度与失败率",
                },
                {
                    "id": "release",
                    "label": "发布回滚",
                    "description": "激活候选并保留完整版本谱系",
                },
            ],
        }


evolution_service = EvolutionService()
