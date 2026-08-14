from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    LearningAttempt,
    LearningKnowledgeNode,
    LearningMemory,
    LearningMistake,
    LearningProject,
    LearningTask,
    ResearchArtifact,
    ResearchLiterature,
    ResearchProject,
    UserAccount,
)
from .common import dumps, loads


OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
FIGURE_SKILL = {
    "name": "scipilot-figure-skill",
    "version": "2.1.1",
    "source": "https://github.com/Haojae/scipilot-figure-skill",
    "principle": "先剖析数据与论证目标，再选择图型、绘制、自检并导出",
}


class AdvancedAcademicService:
    TARGET_DEPTH = {"foundation": 2, "intermediate": 3, "proficient": 4, "advanced": 5}
    CURRENT_DEPTH = {"beginner": 0, "foundation": 1, "intermediate": 2, "advanced": 3}
    DEPTH_LABELS = {
        1: "目标必备概念",
        2: "核心机制拆解",
        3: "方法与最小验证",
        4: "目标场景迁移",
        5: "综合优化与开放问题",
    }

    @staticmethod
    def _learning_meta(node: LearningKnowledgeNode) -> dict[str, Any]:
        return next(
            (item for item in loads(node.source_refs_json, []) if item.get("type") == "learning_path_metadata"),
            {},
        )

    @staticmethod
    def _text_features(text: str) -> set[str]:
        lowered = text.lower()
        features = set(re.findall(r"[a-z0-9_+.#-]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
        features.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return features - {"当前", "目标", "学习", "完成", "知识", "方向", "能够", "一个"}

    @classmethod
    def _goal_alignment(cls, project: LearningProject, node: LearningKnowledgeNode) -> int:
        goal = cls._text_features(" ".join(filter(None, [project.target, project.name, project.description])))
        candidate = cls._text_features(f"{node.title} {node.domain} {cls._learning_meta(node).get('parent_code', '')}")
        if not goal or not candidate:
            return 30
        overlap = len(goal & candidate)
        coverage = overlap / max(1, min(len(goal), len(candidate)))
        direct = 1.0 if any(term in node.title.lower() for term in re.findall(r"[a-z0-9_+.#-]{2,}", project.target.lower())) else 0.0
        return min(100, round(25 + 65 * coverage + 10 * direct))

    async def learning_diagnostic(self, db: AsyncSession, project: LearningProject) -> dict[str, Any]:
        nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id))).all()
        tasks = (await db.scalars(select(LearningTask).where(LearningTask.project_id == project.id))).all()
        attempts = (await db.scalars(select(LearningAttempt).where(LearningAttempt.project_id == project.id))).all()
        mistakes = (await db.scalars(select(LearningMistake).where(LearningMistake.project_id == project.id))).all()
        memories = (await db.scalars(select(LearningMemory).where(LearningMemory.project_id == project.id))).all()
        mastery = sum(node.mastery for node in nodes) / max(1, len(nodes))
        accuracy = sum(item.score for item in attempts) / max(1, len(attempts))
        completion = 100 * sum(item.status == "completed" for item in tasks) / max(1, len(tasks))
        correction = 100 * sum(item.status == "mastered" for item in mistakes) / max(1, len(mistakes)) if mistakes else 100
        engagement = min(100.0, 12 * len(attempts) + 5 * len(memories) + completion * 0.45)
        overall = round(0.34 * mastery + 0.28 * accuracy + 0.20 * completion + 0.10 * correction + 0.08 * engagement, 1)
        level = "起步" if overall < 25 else "基础" if overall < 50 else "进阶" if overall < 75 else "熟练"
        weak = sorted(nodes, key=lambda item: (item.mastery, -self._goal_alignment(project, item), item.order_index))[:5]
        now = datetime.now(timezone.utc)
        overdue = []
        for item in tasks:
            scheduled = item.scheduled_for
            if scheduled and scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if item.status not in {"completed", "skipped"} and scheduled and scheduled < now:
                overdue.append(item)
        profile = loads(project.settings_json, {}).get("direction_profile", {})
        actions = []
        if weak:
            actions.append(f"优先巩固 {'、'.join(item.title for item in weak[:3])}，每个小知识点完成一次复述和一次目标变式练习。")
        if accuracy < 80:
            actions.append("练习正确率尚未达到 80%，先做错因归类，再按 1/3/7 天安排间隔复习。")
        if overdue:
            actions.append(f"当前有 {len(overdue)} 项逾期任务，建议收缩本周并行节点并重新规划路径。")
        if not actions:
            actions.append("当前进展稳定，可进入跨节点综合实践，并用可复现成果验证迁移能力。")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "direction_name": project.name,
            "direction_signature": profile.get("signature"),
            "overall_score": overall,
            "level": level,
            "dimensions": {
                "knowledge_mastery": round(mastery, 1),
                "practice_accuracy": round(accuracy, 1),
                "task_progress": round(completion, 1),
                "mistake_correction": round(correction, 1),
                "learning_engagement": round(engagement, 1),
            },
            "evidence_counts": {"nodes": len(nodes), "tasks": len(tasks), "attempts": len(attempts), "mistakes": len(mistakes), "memories": len(memories)},
            "gaps": [{
                "id": item.id,
                "code": item.code,
                "title": item.title,
                "domain": item.domain,
                "mastery": item.mastery,
                "depth_level": int(self._learning_meta(item).get("depth_level", 1)),
                "goal_alignment": self._goal_alignment(project, item),
            } for item in weak],
            "pace": {"weekly_hours": project.weekly_hours, "overdue_tasks": len(overdue), "deadline": project.deadline, "status": "需要重排" if overdue else "正常"},
            "recommended_actions": actions,
            "limitations": "诊断按原子知识点计算，并依据当前目标、任务、作答、错题和掌握度更新；没有作答时仅是初始估计，不替代教师评价。",
        }

    async def learning_path(self, db: AsyncSession, project: LearningProject) -> dict[str, Any]:
        diagnostic = await self.learning_diagnostic(db, project)
        nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id).order_by(LearningKnowledgeNode.order_index))).all()
        code_map = {item.code: item for item in nodes}
        target_depth = self.TARGET_DEPTH.get(project.target_level, 4)
        baseline_depth = max(1, self.CURRENT_DEPTH.get(project.current_level, 0) + 1)
        overall = float(diagnostic["overall_score"])
        evidence_depth = 1 if overall < 25 else 2 if overall < 50 else 3 if overall < 75 else 4 if overall < 90 else 5
        active_depth = min(target_depth, max(baseline_depth, evidence_depth))

        candidates: list[tuple[int, float, int, LearningKnowledgeNode]] = []
        for item in nodes:
            metadata = self._learning_meta(item)
            depth = int(metadata.get("depth_level", 1))
            unlocked = all(code_map.get(code) is None or code_map[code].mastery >= 60 for code in loads(item.prerequisites_json, []))
            if item.mastery < 80 and unlocked and depth <= active_depth:
                candidates.append((-self._goal_alignment(project, item), item.mastery, item.order_index, item))
        candidates.sort(key=lambda entry: entry[:3])
        current_id = candidates[0][3].id if candidates else (nodes[0].id if nodes else "")
        visual_nodes = []
        for item in nodes:
            prerequisites = loads(item.prerequisites_json, [])
            unlocked = all(code_map.get(code) is None or code_map[code].mastery >= 60 for code in prerequisites)
            metadata = self._learning_meta(item)
            depth = int(metadata.get("depth_level", 1))
            depth_open = depth <= active_depth
            state = "mastered" if item.mastery >= 80 else "current" if item.id == current_id else "ready" if unlocked and depth_open else "locked"
            alignment = self._goal_alignment(project, item)
            reason = (
                "掌握度已达到 80%，保留为后续小知识点的先修证据"
                if state == "mastered" else
                f"与当前目标匹配度 {alignment}%，且先修证据已满足，作为本轮最优先知识点"
                if state == "current" else
                f"当前学习深度为 {active_depth} 级，本知识点属于 {depth} 级，需先用新证据解锁"
                if not depth_open else
                "先修小知识点尚未达到 60% 掌握度"
                if not unlocked else
                f"已解锁；目标匹配度 {alignment}%，可在当前重点后学习"
            )
            visual_nodes.append({
                "id": item.id, "code": item.code, "label": item.title, "domain": item.domain,
                "description": item.description, "mastery": item.mastery, "state": state,
                "order": item.order_index, "resources": loads(item.source_refs_json, []),
                "parent_code": metadata.get("parent_code", item.code),
                "granularity": metadata.get("granularity", "micro"),
                "depth_level": depth,
                "depth_label": metadata.get("depth_label", self.DEPTH_LABELS.get(depth, "目标知识点")),
                "goal_alignment": alignment,
                "adaptation_reason": reason,
                "evidence_requirement": metadata.get("evidence_requirement", "完成一次可检查练习"),
                "recommended_action": metadata.get("evidence_requirement", "完成一次可检查练习") if state == "current" else "先完成前置小知识点" if state == "locked" else "用变式题复核" if state == "mastered" else "进入讲解和最小练习",
            })
        edges = []
        for item in nodes:
            for prerequisite in loads(item.prerequisites_json, []):
                source = code_map.get(prerequisite)
                if source:
                    edges.append({"source": source.id, "target": item.id, "type": "prerequisite", "label": "先修"})
        depth_stages: dict[int, list[str]] = defaultdict(list)
        for item in visual_nodes:
            depth_stages[item["depth_level"]].append(item["id"])
        current_node = next((item for item in visual_nodes if item["id"] == current_id), None)
        return {
            "title": f"{project.name}目标驱动学习路径",
            "subtitle": "路径细化到可讲解、可练习、可测量的原子知识点，并随学习证据自动调整",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diagnostic": diagnostic,
            "goal": project.target or project.name,
            "target_level": project.target_level,
            "target_depth": target_depth,
            "active_depth": active_depth,
            "depth_progress": round(100 * active_depth / max(1, target_depth)),
            "adaptive_summary": f"当前开放到第 {active_depth}/{target_depth} 层；优先选择目标匹配度高、先修已满足且掌握度不足的原子知识点。",
            "next_checkpoint": current_node["evidence_requirement"] if current_node else "完成一次诊断练习以刷新路径",
            "nodes": visual_nodes,
            "edges": edges,
            "stages": [{"name": self.DEPTH_LABELS.get(depth, f"第 {depth} 层"), "depth_level": depth, "node_ids": depth_stages[depth]} for depth in sorted(depth_stages)],
            "current_node_id": current_id,
            "resource_policy": "资源优先来自当前计算机学科包；每个节点保留来源名称和链接。",
        }

    async def companion_session(self, db: AsyncSession, project: LearningProject, minutes: int, mood: str, goal: str) -> dict[str, Any]:
        diagnostic = await self.learning_diagnostic(db, project)
        path = await self.learning_path(db, project)
        current = next((item for item in path["nodes"] if item["id"] == path["current_node_id"]), None)
        topic = current["label"] if current else project.name
        minutes = max(10, min(minutes, 180))
        learn = max(5, round(minutes * 0.35))
        practice = max(5, round(minutes * 0.40))
        reflect = max(3, minutes - learn - practice)
        tone = "今天状态偏低，我们把任务缩小到可以完成的一步。" if mood in {"tired", "stressed"} else "今天按一个完整的小闭环推进。"
        return {
            "message": f"{tone} 当前最值得投入的是“{topic}”，它直接服务于“{project.name}”。",
            "goal": goal.strip() or f"完成{topic}的一次理解—练习—复盘闭环",
            "minutes": minutes,
            "steps": [
                {"title": "导师引导", "minutes": learn, "instruction": f"围绕{topic}复述定义、机制、适用条件和本方向用途；不确定处查看带来源资料。"},
                {"title": "方向练习", "minutes": practice, "instruction": f"完成一道与“{project.name}”有关的变式题或最小实践，并记录输入、输出和验证结果。"},
                {"title": "学伴复盘", "minutes": reflect, "instruction": "记录一个已掌握点、一个错因和下一次复习时间；若仍卡住，进入知识问答继续追问。"},
            ],
            "check_in_question": f"完成后请告诉我：你现在能否不看资料解释“{topic}”如何用于当前方向？",
            "diagnostic_snapshot": {"overall_score": diagnostic["overall_score"], "level": diagnostic["level"], "top_gap": diagnostic["gaps"][0] if diagnostic["gaps"] else None},
        }

    @staticmethod
    def _topic_tokens(item: ResearchLiterature) -> set[str]:
        text = f"{item.title} {' '.join(loads(item.tags_json, []))}".lower()
        stop = {"the", "and", "with", "from", "using", "based", "study", "research", "method", "model", "system", "研究", "方法", "系统", "模型", "分析", "应用", "基于", "一种"}
        english = re.findall(r"[a-z][a-z0-9+.#-]{2,}", text)
        chinese = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
        return {token for token in [*english, *chinese] if token not in stop and len(token) > 1}

    async def frontier_snapshot(self, db: AsyncSession, project: ResearchProject, user: UserAccount, query: str, years: int) -> dict[str, Any]:
        current_year = datetime.now(timezone.utc).year
        items = (await db.scalars(select(ResearchLiterature).where(ResearchLiterature.project_id == project.id, ResearchLiterature.year >= current_year - years + 1).order_by(desc(ResearchLiterature.year), desc(ResearchLiterature.credibility)))).all()
        if not items:
            raise HTTPException(status_code=422, detail="当前时间范围没有文献，请先执行前沿检索")
        token_docs: dict[str, list[ResearchLiterature]] = defaultdict(list)
        item_tokens: dict[str, set[str]] = {}
        for item in items:
            tokens = self._topic_tokens(item)
            item_tokens[item.id] = tokens
            for token in tokens:
                token_docs[token].append(item)
        topics = sorted(token_docs, key=lambda token: (-len(token_docs[token]), token))[:12]
        midpoint = current_year - max(1, years // 2)
        topic_nodes = []
        for token in topics:
            docs = token_docs[token]
            recent = sum((item.year or 0) >= midpoint for item in docs)
            earlier = len(docs) - recent
            growth = round((recent + 1) / (earlier + 1), 2)
            topic_nodes.append({"id": f"topic:{token}", "type": "topic", "label": token, "count": len(docs), "growth": growth, "heat": round(min(100, len(docs) * 12 + growth * 15), 1)})
        paper_nodes = [{"id": item.id, "type": "paper", "label": item.title, "year": item.year, "authors": item.authors, "doi": item.doi, "url": item.url, "credibility": item.credibility, "source": item.source} for item in items[:35]]
        edges = []
        for item in items[:35]:
            for token in topics:
                if token in item_tokens[item.id]:
                    edges.append({"source": f"topic:{token}", "target": item.id, "relation": "主题归属", "strength": round(0.5 + min(0.45, len(token) * 0.04), 2)})
        yearly = Counter(item.year for item in items if item.year)
        timeline = [{"year": year, "papers": yearly.get(year, 0), "top_topics": [token for token in topics if any(item.year == year for item in token_docs[token])][:5]} for year in range(current_year - years + 1, current_year + 1)]
        rising = sorted(topic_nodes, key=lambda item: (-item["growth"], -item["count"]))[:5]
        payload = {
            "title": f"{project.name}领域前沿与热点图谱",
            "query": query or project.research_question or project.description,
            "period": {"from": current_year - years + 1, "to": current_year},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": f"本次基于 {len(items)} 篇项目文献形成前沿快照。当前高频主题为：{'、'.join(item['label'] for item in topic_nodes[:5]) or '待积累'}；增长较快的主题为：{'、'.join(item['label'] for item in rising) or '待积累'}。主题热度是本项目题录的相对指标，不等同于全领域引用量。",
            "nodes": [*topic_nodes, *paper_nodes], "edges": edges, "timeline": timeline,
            "hot_topics": topic_nodes, "rising_topics": rising,
            "sources": [{"id": item.id, "title": item.title, "year": item.year, "doi": item.doi, "url": item.url, "source": item.source, "credibility": item.credibility} for item in items],
            "methodology": "主题由题名与项目标签的词项共现计算；增长率使用时间窗前后半段的平滑频次比。所有趋势属于项目内证据推断。",
        }
        artifact = ResearchArtifact(project_id=project.id, created_by=user.id, kind="frontier-snapshot", title=payload["title"], content=dumps(payload), source_ids_json=dumps([item.id for item in items]), metadata_json=dumps({"traceable": True, "years": years, "query": payload["query"]}))
        db.add(artifact)
        await db.flush()
        return {**payload, "artifact_id": artifact.id}

    @staticmethod
    def parse_dataset(filename: str, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
        if len(data) > 8_000_000:
            raise HTTPException(status_code=413, detail="科研数据文件不能超过 8 MB")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
        suffix = filename.lower().rsplit(".", 1)[-1]
        if suffix == "json":
            raw = json.loads(text)
            records = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
            if not all(isinstance(item, dict) for item in records):
                raise HTTPException(status_code=422, detail="JSON 数据应为对象数组或包含 data 对象数组")
            fields = list(dict.fromkeys(key for item in records for key in item))
        else:
            delimiter = "\t" if suffix == "tsv" else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            fields = list(reader.fieldnames or [])
            records = list(reader)
        if not fields or not records:
            raise HTTPException(status_code=422, detail="数据文件没有可分析的表头或记录")
        return records[:5000], fields[:120]

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            number = float(str(value).replace(",", ""))
            return number if math.isfinite(number) else None
        except ValueError:
            return None

    def profile_dataset(self, records: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
        columns = []
        numeric_values: dict[str, list[float]] = {}
        for field in fields:
            raw = [row.get(field) for row in records]
            numbers = [number for value in raw if (number := self._number(value)) is not None]
            non_empty = [value for value in raw if value is not None and str(value).strip()]
            numeric = bool(non_empty) and len(numbers) / len(non_empty) >= 0.8
            item: dict[str, Any] = {"name": field, "type": "numeric" if numeric else "categorical", "count": len(records), "non_null": len(non_empty), "missing": len(records) - len(non_empty), "missing_rate": round(100 * (len(records) - len(non_empty)) / len(records), 1), "unique": len({str(value) for value in non_empty})}
            if numeric:
                numeric_values[field] = numbers
                mean = statistics.fmean(numbers)
                item.update({"min": min(numbers), "max": max(numbers), "mean": round(mean, 6), "median": round(statistics.median(numbers), 6), "sd": round(statistics.stdev(numbers), 6) if len(numbers) > 1 else 0})
            else:
                item["top_values"] = [{"value": value, "count": count} for value, count in Counter(str(value) for value in non_empty).most_common(8)]
            columns.append(item)
        correlations = []
        numeric_fields = list(numeric_values)
        for index, left in enumerate(numeric_fields):
            for right in numeric_fields[index + 1:]:
                pairs = [(self._number(row.get(left)), self._number(row.get(right))) for row in records]
                pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
                if len(pairs) < 3:
                    continue
                xs, ys = zip(*pairs)
                mx, my = statistics.fmean(xs), statistics.fmean(ys)
                denominator = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
                correlations.append({"x": left, "y": right, "r": round(sum((x - mx) * (y - my) for x, y in pairs) / denominator, 4) if denominator else 0, "n": len(pairs)})
        return {"rows": len(records), "columns": columns, "numeric_fields": numeric_fields, "categorical_fields": [item["name"] for item in columns if item["type"] == "categorical"], "correlations": correlations}

    async def store_dataset(self, db: AsyncSession, project: ResearchProject, user: UserAccount, filename: str, data: bytes) -> dict[str, Any]:
        records, fields = self.parse_dataset(filename, data)
        profile = self.profile_dataset(records, fields)
        recommendations = self.chart_recommendations(profile)
        strongest = sorted(profile["correlations"], key=lambda item: abs(item["r"]), reverse=True)[:5]
        quality_warnings = [
            f"{item['name']} 缺失率为 {item['missing_rate']}%，生成结论前应说明缺失值处理策略。"
            for item in profile["columns"] if item["missing_rate"] > 0
        ]
        insights = []
        for item in strongest[:3]:
            direction = "正" if item["r"] >= 0 else "负"
            strength = "强" if abs(item["r"]) >= 0.7 else "中等" if abs(item["r"]) >= 0.4 else "弱"
            insights.append(
                f"{item['x']} 与 {item['y']} 在 {item['n']} 个完整观测中呈{strength}{direction}相关（r={item['r']}）；相关不等于因果，仍需结合研究设计与显著性检验。"
            )
        if not insights:
            insights.append("当前数据不足以形成稳定的变量相关判断；建议补充连续变量或扩大有效样本量。")
        payload = {
            "filename": filename,
            "profile": profile,
            "records": records,
            "fields": fields,
            "recommendations": recommendations,
            "insights": insights,
            "quality_warnings": quality_warnings,
            "skill": FIGURE_SKILL,
        }
        artifact = ResearchArtifact(project_id=project.id, created_by=user.id, kind="research-dataset", title=filename, content=dumps(payload), source_ids_json="[]", metadata_json=dumps({"rows": profile["rows"], "columns": len(fields), "skill": FIGURE_SKILL["name"]}))
        db.add(artifact)
        await db.flush()
        return {"id": artifact.id, "title": artifact.title, **payload}

    @staticmethod
    def chart_recommendations(profile: dict[str, Any]) -> list[dict[str, str]]:
        numeric, categorical = profile["numeric_fields"], profile["categorical_fields"]
        results = []
        if categorical and numeric:
            results.append({"chart_type": "strip", "title": "分组原始点图", "reason": "展示每组原始观测，避免均值柱掩盖样本量与分布。"})
        if len(numeric) >= 2:
            results.append({"chart_type": "scatter", "title": "变量关系散点图", "reason": "用于检验两个连续变量之间的关系，并保留每个样本。"})
            results.append({"chart_type": "correlation", "title": "相关性热力图", "reason": "适合概览多个连续变量之间的相关结构。"})
        if numeric:
            results.append({"chart_type": "histogram", "title": "分布直方图", "reason": "用于观察偏态、多峰与异常值，不用均值替代分布。"})
        if categorical:
            results.append({"chart_type": "bar", "title": "分类频数横向柱状图", "reason": "使用长度表达占比，比饼图更准确。"})
        return results[:4]

    def choose_chart(self, profile: dict[str, Any], argument: str, chart_type: str, x: str, y: str) -> tuple[str, str, list[str]]:
        numeric, categorical = profile["numeric_fields"], profile["categorical_fields"]
        warnings = []
        if chart_type in {"pie", "3d", "dual_y", "jet"}:
            warnings.append("SciPilot 质量门禁已拦截不适合学术论证的图型，自动改用更诚实的二维图。")
            chart_type = "auto"
        if chart_type == "auto":
            if "相关" in argument and len(numeric) >= 3:
                chart_type = "correlation"
            elif x in categorical and y in numeric:
                chart_type = "strip"
            elif x in numeric and y in numeric:
                chart_type = "scatter"
            elif numeric:
                chart_type = "histogram"
            else:
                chart_type = "bar"
        reason = {"strip": "展示每个原始点，适合组间比较并避免小样本均值柱误导。", "scatter": "两个连续变量使用散点表达关系，不暗示不存在的顺序。", "correlation": "使用感知均匀的发散色阶概览变量相关结构，并保留数值标注。", "histogram": "直接呈现连续变量分布、偏态和多峰。", "bar": "分类频数使用按值排序的横向柱，比饼图更易准确比较。"}.get(chart_type, "依据数据类型和论证目标选择。")
        return chart_type, reason, warnings

    def publication_svg(self, payload: dict[str, Any], argument: str, chart_type: str, x: str, y: str, group: str, journal: str, title: str) -> tuple[str, dict[str, Any]]:
        profile, records = payload["profile"], payload["records"]
        chart_type, reason, warnings = self.choose_chart(profile, argument, chart_type, x, y)
        numeric, categorical = profile["numeric_fields"], profile["categorical_fields"]
        x = x or (categorical[0] if chart_type in {"strip", "bar"} and categorical else numeric[0] if numeric else payload["fields"][0])
        y = y or (numeric[0] if numeric else "")
        width, height = 960, 600
        left, right, top, bottom = 92, 42, 72, 78
        plot_w, plot_h = width - left - right, height - top - bottom
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#ffffff"/>', f'<text x="{left}" y="34" font-family="Arial,Microsoft YaHei" font-size="20" font-weight="700" fill="#111111">{escape(title or argument or "科研数据分析")}</text>', f'<text x="{left}" y="55" font-family="Arial,Microsoft YaHei" font-size="11" fill="#555555">{escape(reason)}</text>', f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#111"/>', f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111"/>']

        def scale(value: float, low: float, high: float, start: float, size: float, invert: bool = False) -> float:
            ratio = 0.5 if high == low else (value - low) / (high - low)
            return start + size * (1 - ratio if invert else ratio)

        if chart_type == "scatter" and len(numeric) >= 2:
            x = x if x in numeric else numeric[0]; y = y if y in numeric and y != x else next(item for item in numeric if item != x)
            points = [(self._number(row.get(x)), self._number(row.get(y))) for row in records]
            points = [(a, b) for a, b in points if a is not None and b is not None]
            xmin, xmax = min(a for a, _ in points), max(a for a, _ in points); ymin, ymax = min(b for _, b in points), max(b for _, b in points)
            for index, (a, b) in enumerate(points):
                parts.append(f'<circle cx="{scale(a,xmin,xmax,left,plot_w):.2f}" cy="{scale(b,ymin,ymax,top,plot_h,True):.2f}" r="4" fill="{OKABE_ITO[index % 6]}" fill-opacity="0.68"/>')
        elif chart_type == "strip" and categorical and numeric:
            x = x if x in categorical else categorical[0]; y = y if y in numeric else numeric[0]
            categories = list(dict.fromkeys(str(row.get(x, "")) for row in records))[:12]
            values = [self._number(row.get(y)) for row in records]; values = [value for value in values if value is not None]
            ymin, ymax = min(values), max(values)
            for index, category in enumerate(categories):
                category_values = [self._number(row.get(y)) for row in records if str(row.get(x, "")) == category]
                category_values = [value for value in category_values if value is not None]
                px = left + (index + 0.5) * plot_w / len(categories)
                for point_index, value in enumerate(category_values):
                    jitter = ((point_index * 37) % 17 - 8) * 1.2
                    parts.append(f'<circle cx="{px+jitter:.2f}" cy="{scale(value,ymin,ymax,top,plot_h,True):.2f}" r="4" fill="{OKABE_ITO[index % 6]}" fill-opacity="0.72"/>')
                parts.append(f'<text x="{px:.2f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Arial,Microsoft YaHei" font-size="10">{escape(category[:14])}</text>')
        elif chart_type == "correlation" and len(numeric) >= 2:
            fields = numeric[:10]; matrix = {(item["x"], item["y"]): item["r"] for item in profile["correlations"]}
            cell = min(plot_w, plot_h) / len(fields)
            for row_index, row_name in enumerate(fields):
                for col_index, col_name in enumerate(fields):
                    r = 1.0 if row_name == col_name else matrix.get((row_name, col_name), matrix.get((col_name, row_name), 0))
                    blue = int(245 - max(0, r) * 145); red = int(245 - max(0, -r) * 145)
                    color = f'#{red:02x}{min(red,blue):02x}{blue:02x}'
                    px, py = left + col_index * cell, top + row_index * cell
                    parts.append(f'<rect x="{px:.2f}" y="{py:.2f}" width="{cell:.2f}" height="{cell:.2f}" fill="{color}" stroke="#fff"/><text x="{px+cell/2:.2f}" y="{py+cell/2+4:.2f}" text-anchor="middle" font-family="Arial" font-size="10">{r:.2f}</text>')
                parts.append(f'<text x="{left-8}" y="{top+row_index*cell+cell/2+4:.2f}" text-anchor="end" font-family="Arial,Microsoft YaHei" font-size="9">{escape(row_name[:12])}</text>')
            for index, field in enumerate(fields):
                parts.append(f'<text x="{left+index*cell+cell/2:.2f}" y="{top+len(fields)*cell+18:.2f}" text-anchor="end" transform="rotate(-35 {left+index*cell+cell/2:.2f} {top+len(fields)*cell+18:.2f})" font-family="Arial,Microsoft YaHei" font-size="9">{escape(field[:12])}</text>')
        elif chart_type == "histogram" and numeric:
            x = x if x in numeric else numeric[0]; values = [self._number(row.get(x)) for row in records]; values = [value for value in values if value is not None]
            low, high = min(values), max(values); bins = max(5, min(20, round(math.sqrt(len(values))))); counts = [0] * bins
            for value in values:
                counts[min(bins - 1, int((value - low) / max(high - low, 1e-12) * bins))] += 1
            max_count = max(counts)
            for index, count in enumerate(counts):
                bar_w = plot_w / bins - 2; bar_h = plot_h * count / max_count
                parts.append(f'<rect x="{left+index*plot_w/bins+1:.2f}" y="{top+plot_h-bar_h:.2f}" width="{bar_w:.2f}" height="{bar_h:.2f}" fill="{OKABE_ITO[0]}" fill-opacity="0.78"/>')
        else:
            x = x if x in categorical else categorical[0]
            counts = Counter(str(row.get(x, "")) for row in records).most_common(12); max_count = max(count for _, count in counts)
            bar_h = plot_h / max(1, len(counts)) - 5
            for index, (label, count) in enumerate(counts):
                py = top + index * (bar_h + 5)
                parts.append(f'<text x="{left-8}" y="{py+bar_h*.7:.2f}" text-anchor="end" font-family="Arial,Microsoft YaHei" font-size="10">{escape(label[:16])}</text><rect x="{left}" y="{py:.2f}" width="{plot_w*count/max_count:.2f}" height="{bar_h:.2f}" fill="{OKABE_ITO[index%6]}"/><text x="{left+plot_w*count/max_count+6:.2f}" y="{py+bar_h*.7:.2f}" font-family="Arial" font-size="10">{count}</text>')
        parts.extend([f'<text x="{left+plot_w/2}" y="{height-24}" text-anchor="middle" font-family="Arial,Microsoft YaHei" font-size="12">{escape(x)}</text>', f'<text x="22" y="{top+plot_h/2}" text-anchor="middle" transform="rotate(-90 22 {top+plot_h/2})" font-family="Arial,Microsoft YaHei" font-size="12">{escape(y or "频数")}</text>', f'<text x="{width-30}" y="{height-12}" text-anchor="end" font-family="Arial" font-size="8" fill="#666">{escape(journal)} · SciPilot QA</text>', '</svg>'])
        spec = {"chart_type": chart_type, "x": x, "y": y, "group": group, "argument": argument, "reason": reason, "warnings": warnings, "journal": journal, "formats": ["svg"], "colorblind_safe": True, "vector": True, "skill": FIGURE_SKILL, "quality_checks": ["白底黑字", "无 3D/饼图/双 Y 轴", "Okabe-Ito 配色", "矢量输出", "标题与坐标标签已转义"]}
        return "".join(parts), spec

    async def create_publication_figure(self, db: AsyncSession, project: ResearchProject, user: UserAccount, dataset: ResearchArtifact, request: dict[str, Any]) -> dict[str, Any]:
        payload = loads(dataset.content, {})
        svg, spec = self.publication_svg(payload, request.get("argument", ""), request.get("chart_type", "auto"), request.get("x", ""), request.get("y", ""), request.get("group", ""), request.get("journal", "general"), request.get("title", ""))
        result = {"svg": svg, "spec": spec, "dataset_id": dataset.id, "dataset_title": dataset.title, "caption_template": f"{request.get('title') or request.get('argument') or dataset.title}。图型依据数据结构与论证目标选择；请在图注中补充样本量、误差类型、统计检验与校正方法。"}
        artifact = ResearchArtifact(project_id=project.id, created_by=user.id, kind="publication-figure", title=request.get("title") or f"{dataset.title}论文图表", content=dumps(result), source_ids_json=dumps([dataset.id]), metadata_json=dumps(spec))
        db.add(artifact)
        await db.flush()
        return {"id": artifact.id, "title": artifact.title, **result}

    async def list_artifacts(self, db: AsyncSession, project_id: str, kinds: list[str]) -> list[dict[str, Any]]:
        items = (await db.scalars(select(ResearchArtifact).where(ResearchArtifact.project_id == project_id, ResearchArtifact.kind.in_(kinds)).order_by(desc(ResearchArtifact.created_at)))).all()
        result = []
        for item in items:
            result.append({"id": item.id, "title": item.title, "kind": item.kind, "created_at": item.created_at, "updated_at": item.updated_at, "payload": loads(item.content, {}), "metadata": loads(item.metadata_json, {}), "source_ids": loads(item.source_ids_json, [])})
        return result


advanced_academic_service = AdvancedAcademicService()
