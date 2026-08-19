from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    AgentDefinition,
    TeachingDocument,
    TeachingStudioAnnotation,
    TeachingStudioDocument,
    TeachingStudioSession,
    TeachingStudioTurn,
    UserAccount,
)
from .agents import agent_engine
from .common import dumps, loads
from .knowledge_processing import extract_sections
from .teaching_space import SUPPORTED_SUFFIXES, teaching_space_service


class StandaloneTeachingService:
    """Independent one-to-one classroom; it never reads or creates a learning project."""

    @property
    def root(self) -> Path:
        path = settings.workspace_root / "teaching-studio"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def migrate_legacy_documents(
        self, db: AsyncSession, user: UserAccount
    ) -> list[TeachingStudioDocument]:
        """Preserve already imported classroom files while removing project coupling."""

        legacy_items = (
            await db.scalars(
                select(TeachingDocument).where(TeachingDocument.owner_id == user.id)
            )
        ).all()
        known_hashes = set(
            (
                await db.scalars(
                    select(TeachingStudioDocument.file_hash).where(
                        TeachingStudioDocument.owner_id == user.id
                    )
                )
            ).all()
        )
        migrated: list[TeachingStudioDocument] = []
        for legacy in legacy_items:
            if legacy.file_hash in known_hashes:
                continue
            item = TeachingStudioDocument(
                owner_id=user.id,
                title=legacy.title,
                filename=legacy.filename,
                mime_type=legacy.mime_type,
                source_path=legacy.source_path,
                rendered_path=legacy.rendered_path,
                file_hash=legacy.file_hash,
                page_count=legacy.page_count,
                sections_json=legacy.sections_json,
                metadata_json=dumps({
                    **loads(legacy.metadata_json, {}),
                    "migrated_from_project_classroom": True,
                }),
                status=legacy.status,
            )
            db.add(item)
            migrated.append(item)
            known_hashes.add(legacy.file_hash)
        if migrated:
            await db.flush()
        return migrated

    async def document_access(
        self, db: AsyncSession, document_id: str, user: UserAccount
    ) -> TeachingStudioDocument:
        item = await db.get(TeachingStudioDocument, document_id)
        if not item:
            raise LookupError("教学文档不存在")
        if item.owner_id != user.id:
            raise PermissionError("无权访问该教学文档")
        return item

    async def session_access(
        self, db: AsyncSession, session_id: str, user: UserAccount
    ) -> TeachingStudioSession:
        item = await db.get(TeachingStudioSession, session_id)
        if not item:
            raise LookupError("教学会话不存在")
        if item.owner_id != user.id:
            raise PermissionError("无权访问该教学会话")
        return item

    @staticmethod
    def document_payload(item: TeachingStudioDocument) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "filename": item.filename,
            "mime_type": item.mime_type,
            "page_count": item.page_count,
            "sections": loads(item.sections_json, []),
            "metadata": loads(item.metadata_json, {}),
            "status": item.status,
            "has_rendered_file": bool(item.rendered_path),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def turn_payload(item: TeachingStudioTurn) -> dict[str, Any]:
        return {
            "id": item.id,
            "role": item.role,
            "content": item.content,
            "page": item.page,
            "citations": loads(item.citations_json, []),
            "commands": loads(item.commands_json, []),
            "metadata": loads(item.metadata_json, {}),
            "created_at": item.created_at,
        }

    async def session_payload(
        self, db: AsyncSession, item: TeachingStudioSession
    ) -> dict[str, Any]:
        document = await db.get(TeachingStudioDocument, item.document_id)
        turns = (
            await db.scalars(
                select(TeachingStudioTurn)
                .where(TeachingStudioTurn.session_id == item.id)
                .order_by(TeachingStudioTurn.created_at)
            )
        ).all()
        annotations = (
            await db.scalars(
                select(TeachingStudioAnnotation)
                .where(TeachingStudioAnnotation.session_id == item.id)
                .order_by(TeachingStudioAnnotation.created_at)
            )
        ).all()
        agent = await db.get(AgentDefinition, item.agent_id) if item.agent_id else None
        return {
            "id": item.id,
            "document_id": item.document_id,
            "agent_id": item.agent_id,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
            } if agent else None,
            "status": item.status,
            "current_page": item.current_page,
            "current_unit": item.current_unit,
            "progress": item.progress,
            "settings": loads(item.settings_json, {}),
            "lesson_plan": loads(item.lesson_plan_json, []),
            "document": self.document_payload(document) if document else None,
            "turns": [self.turn_payload(turn) for turn in turns],
            "annotations": [
                {
                    "id": annotation.id,
                    "page": annotation.page,
                    "author": annotation.author,
                    "kind": annotation.kind,
                    "payload": loads(annotation.payload_json, {}),
                }
                for annotation in annotations
            ],
        }

    async def store_document(
        self,
        db: AsyncSession,
        user: UserAccount,
        filename: str,
        data: bytes,
    ) -> TeachingStudioDocument:
        safe_name = Path(filename.replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("智能讲解教室仅支持 PDF 和 PPTX")
        digest = hashlib.sha256(data).hexdigest()
        existing = await db.scalar(
            select(TeachingStudioDocument).where(
                TeachingStudioDocument.owner_id == user.id,
                TeachingStudioDocument.file_hash == digest,
            )
        )
        if existing:
            return existing
        sections, mime = extract_sections(safe_name, data)
        if not sections:
            raise ValueError("文档中没有可分析的页面")
        folder = self.root / user.id / digest[:16]
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / f"source{suffix}"
        source.write_bytes(data)
        rendered: Path | None = source if suffix == ".pdf" else None
        conversion_note = "PDF 原文件直接渲染"
        if suffix == ".pptx":
            rendered, conversion_note = teaching_space_service._convert_pptx(source, folder)
        page_count = len(sections)
        if rendered and rendered.suffix.lower() == ".pdf":
            try:
                page_count = len(PdfReader(str(rendered)).pages)
            except Exception:
                pass
        section_payload = [
            {
                "index": index,
                "page": index,
                "title": section.heading or section.locator or f"第 {index} 页",
                "text": section.text[:30000],
                "locator": section.locator,
                "metadata": section.metadata,
            }
            for index, section in enumerate(sections, 1)
        ]
        item = TeachingStudioDocument(
            owner_id=user.id,
            title=Path(safe_name).stem[:240],
            filename=safe_name[:500],
            mime_type=mime,
            source_path=str(source),
            rendered_path=str(rendered) if rendered else None,
            file_hash=digest,
            page_count=page_count,
            sections_json=dumps(section_payload),
            metadata_json=dumps({
                "conversion_note": conversion_note,
                "source_kind": suffix.removeprefix("."),
                "standalone": True,
            }),
            status="ready" if rendered or suffix == ".pptx" else "failed",
        )
        db.add(item)
        await db.flush()
        return item

    @staticmethod
    def create_lesson_plan(document: TeachingStudioDocument) -> list[dict[str, Any]]:
        return [
            {
                "unit": index,
                "page": int(section.get("page", index)),
                "title": str(section.get("title") or f"第 {index} 页")[:160],
                "summary": re.sub(r"\s+", " ", str(section.get("text", "")))[:160],
                "estimated_minutes": 2 if len(str(section.get("text", ""))) < 500 else 4,
                "status": "pending",
            }
            for index, section in enumerate(loads(document.sections_json, []), 1)
        ]

    @staticmethod
    def _micro_turn(text: str, limit: int = 180) -> str:
        clean = re.sub(r"\n{3,}", "\n\n", text.strip())
        if len(clean) <= limit:
            return clean
        candidates = list(re.finditer(r"[。！？!?；;]\s*", clean[: limit + 1]))
        cut = candidates[-1].end() if candidates and candidates[-1].end() >= 60 else limit
        return clean[:cut].rstrip() + ("" if clean[cut - 1] in "。！？!?；;" else "……")

    @staticmethod
    def _page_section(document: TeachingStudioDocument, page: int) -> dict[str, Any]:
        sections = loads(document.sections_json, [])
        if not sections:
            return {"page": 1, "title": "当前页面", "text": ""}
        return sections[min(max(page, 1), len(sections)) - 1]

    @staticmethod
    def _knowledge_units(page_text: str, title: str) -> list[str]:
        """Split a page into small units so 'continue' advances instead of repeating it."""

        clean = re.sub(r"\s+", " ", page_text).strip()
        units = [
            value.strip(" ·—-：:")
            for value in re.split(r"(?<=[。！？!?；;])\s*|\s{2,}|[•●▪]\s*", clean)
            if len(value.strip()) >= 8
        ]
        return units[:20] or [title or "当前页面的核心概念"]

    @staticmethod
    def _extract_formula(text: str) -> str:
        patterns = (
            r"\$([^$\n]{3,90})\$",
            r"\\\(([^\n]{3,90})\\\)",
            r"([A-Za-z][A-Za-z0-9_()]{0,18}\s*=\s*[^，。；;\n]{2,70})",
            r"([^，。；;\n]{1,30}\s*(?:→|⇒|≈|≤|≥)\s*[^，。；;\n]{1,40})",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()[:90]
        return ""

    @staticmethod
    def _parse_agent_plan(raw: str) -> dict[str, Any]:
        """Accept strict JSON or a fenced JSON object while safely falling back to speech."""

        value = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", value, re.S)
        candidate = fenced.group(1) if fenced else value
        if not candidate.startswith("{"):
            embedded = re.search(r"\{.*\}", candidate, re.S)
            candidate = embedded.group(0) if embedded else ""
        if candidate:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except (TypeError, ValueError):
                pass
        return {"speech": value}

    @staticmethod
    def _sanitize_board_actions(
        actions: Any, *, page: int, focus: str, formula: str, note: str
    ) -> list[dict[str, Any]]:
        allowed = {"highlight_text", "circle_text", "write_note", "write_formula"}
        result: list[dict[str, Any]] = []
        if isinstance(actions, list):
            for item in actions[:2]:
                if not isinstance(item, dict) or item.get("type") not in allowed:
                    continue
                command_type = str(item["type"])
                target = str(item.get("target") or focus).strip()[:80]
                text = str(item.get("text") or (formula if command_type == "write_formula" else note)).strip()[:90]
                if command_type in {"highlight_text", "circle_text"} and not target:
                    continue
                if command_type in {"write_note", "write_formula"} and not text:
                    continue
                try:
                    x = float(item.get("x", 0.57))
                    y = float(item.get("y", 0.16 + len(result) * 0.08))
                except (TypeError, ValueError):
                    x, y = 0.57, 0.16 + len(result) * 0.08
                result.append({
                    "type": command_type,
                    "page": page,
                    "target": target,
                    "text": text,
                    "color": str(item.get("color") or "#1769c2")[:20],
                    "x": min(0.82, max(0.05, x)),
                    "y": min(0.88, max(0.08, y)),
                })
        return result

    @classmethod
    def _fallback_teacher_plan(
        cls,
        *,
        action: str,
        message: str,
        unit: str,
        focus: str,
        formula: str,
        title: str,
        turn_index: int,
    ) -> dict[str, Any]:
        short_unit = cls._micro_turn(unit, 95).rstrip("……")
        if action == "ask" and message.strip():
            speech = (
                f"你问到关键处了。这里要抓住“{focus or title}”：{short_unit}。"
                "先别急着记结论，你试着说说其中哪个条件最重要？"
            )
        elif turn_index == 0:
            speech = (
                f"我们先看“{focus or title}”。{short_unit}。"
                "我已经在课件上标出来了，你先用自己的话告诉我：它描述的对象是什么？"
            )
        else:
            speech = (
                f"好，我们只往前走一步。刚才的基础上，再看“{focus or title}”：{short_unit}。"
                "如果把这个条件换掉，你觉得结论还会成立吗？"
            )
        if formula:
            actions = [
                {"type": "circle_text", "target": focus},
                {"type": "write_formula", "text": formula, "x": 0.56, "y": 0.17},
            ]
        elif turn_index % 3 == 0:
            actions = [
                {"type": "highlight_text", "target": focus},
                {"type": "write_note", "text": f"关键：{focus or title}", "x": 0.58, "y": 0.16},
            ]
        elif turn_index % 3 == 1:
            actions = [{"type": "circle_text", "target": focus}]
        else:
            actions = [{"type": "write_note", "text": f"条件 → {focus or title}"}]
        return {"speech": speech, "board_actions": actions}

    async def default_agent_id(self, db: AsyncSession) -> str | None:
        preferred = await db.scalar(
            select(AgentDefinition).where(
                AgentDefinition.slug == "learning-socratic-tutor",
                AgentDefinition.status == "active",
            )
        )
        if preferred:
            return preferred.id
        first = await db.scalar(
            select(AgentDefinition)
            .where(AgentDefinition.status == "active")
            .order_by(AgentDefinition.created_at)
        )
        return first.id if first else None

    async def create_session(
        self,
        db: AsyncSession,
        document: TeachingStudioDocument,
        user: UserAccount,
        settings_data: dict[str, Any],
        agent_id: str | None,
    ) -> TeachingStudioSession:
        selected_agent = agent_id or await self.default_agent_id(db)
        session = TeachingStudioSession(
            document_id=document.id,
            owner_id=user.id,
            agent_id=selected_agent,
            status="ready",
            current_page=1,
            settings_json=dumps(settings_data),
            lesson_plan_json=dumps(self.create_lesson_plan(document)),
        )
        db.add(session)
        await db.flush()
        db.add(
            TeachingStudioTurn(
                session_id=session.id,
                role="assistant",
                page=1,
                content=self._micro_turn(
                    f"课件《{document.title}》已经准备好。我会逐页讲解，每次只推进一个要点。你可以随时提问、暂停或停止。准备好后点击开始。"
                ),
                metadata_json=dumps({
                    "kind": "greeting",
                    "source_traceable": True,
                    "standalone": True,
                }),
            )
        )
        await db.flush()
        return session

    async def teach(
        self,
        db: AsyncSession,
        session: TeachingStudioSession,
        user: UserAccount,
        *,
        message: str,
        action: str,
        page: int,
    ) -> TeachingStudioTurn:
        document = await db.get(TeachingStudioDocument, session.document_id)
        if not document:
            raise LookupError("教学文档不存在")
        page = min(max(1, page), max(1, document.page_count))
        session.current_page = page
        session.current_unit = page
        session.status = "explaining"
        session.progress = min(99, round(100 * (page - 1) / max(1, document.page_count)))
        section = self._page_section(document, page)
        if action == "ask" and message.strip():
            db.add(
                TeachingStudioTurn(
                    session_id=session.id,
                    role="user",
                    page=page,
                    content=message.strip(),
                )
            )
            await db.flush()
        page_text = re.sub(r"\s+", " ", str(section.get("text", ""))).strip()
        history_rows = (
            await db.scalars(
                select(TeachingStudioTurn)
                .where(TeachingStudioTurn.session_id == session.id)
                .order_by(TeachingStudioTurn.created_at.desc())
                .limit(20)
            )
        ).all()
        page_teacher_turns = [
            turn
            for turn in history_rows
            if turn.role == "assistant"
            and turn.page == page
            and loads(turn.metadata_json, {}).get("kind") != "greeting"
        ]
        turn_index = len(page_teacher_turns)
        title = str(section.get("title") or f"第 {page} 页")
        units = self._knowledge_units(page_text, title)
        unit = units[min(turn_index, len(units) - 1)]
        focus = re.sub(r"^[\d.、()（）一二三四五六七八九十]+\s*", "", unit)[:32].strip()
        formula = self._extract_formula(unit) or self._extract_formula(page_text)
        note = f"关键：{focus or title}"[:80]
        intent = message.strip() or (
            "从当前页尚未讲过的内容中选择一个最小知识点继续讲解"
        )
        fallback_plan = self._fallback_teacher_plan(
            action=action,
            message=message,
            unit=unit,
            focus=focus,
            formula=formula,
            title=title,
            turn_index=turn_index,
        )
        agent = await db.get(AgentDefinition, session.agent_id) if session.agent_id else None
        prompt = (
            f"你是一位正在与学生一对一上课的教师。课件《{document.title}》第{page}页，标题：{title}。\n"
            f"本轮唯一讲授单元：{unit[:1200]}\n页面上下文：{page_text[:5000]}\n"
            f"学生刚才说：{intent}\n这是本页第 {turn_index + 1} 个教师轮次。\n"
            "像真实教师一样承接学生上一句话：先直接回应，再用一个因果关系、对比例子或最小推导解释；"
            "不要重复页面全文，不要说空泛套话，不要一次讲多个知识点，最后提出一个能检查理解的短问题。"
            "口语化但专业，70至150个汉字，最多180字。不要关联学习项目或学习方向。\n"
            "同时判断是否需要板书。只在确实帮助理解时选择0至2个动作："
            "highlight_text（高亮原文）、circle_text（圈出术语）、write_note（写简短草稿）、"
            "write_formula（写公式）。target必须逐字取自本轮讲授单元，text不超过40字。\n"
            "严格只返回一个JSON对象，不要Markdown："
            '{"speech":"教师说的话","board_actions":[{"type":"highlight_text","target":"原文片段"}]}'
        )
        plan: dict[str, Any] = {}
        if agent and agent.provider != "demo":
            try:
                history = [
                    {"role": turn.role, "content": turn.content}
                    for turn in reversed(history_rows[:8])
                ]
                run = await agent_engine.run(
                    db,
                    agent.id,
                    prompt,
                    {"user_id": user.id},
                    conversation_messages=history,
                )
                plan = self._parse_agent_plan(run.output_text)
            except Exception:
                plan = {}
        answer = str(plan.get("speech") or fallback_plan["speech"]).strip()
        board_actions = self._sanitize_board_actions(
            plan.get("board_actions") or fallback_plan["board_actions"],
            page=page,
            focus=focus,
            formula=formula,
            note=note,
        )
        commands: list[dict[str, Any]] = []
        if focus:
            commands.append({
                "type": "focus_text",
                "page": page,
                "text": focus,
                "color": "#1769c2",
            })
        commands.extend(board_actions)
        citations = [{
            "id": f"{document.id}-page-{page}",
            "title": f"{document.title} · 第 {page} 页",
            "source": f"本地课件：{document.filename}",
            "excerpt": unit[:420],
        }]
        turn = TeachingStudioTurn(
            session_id=session.id,
            role="assistant",
            page=page,
            content=self._micro_turn(answer),
            citations_json=dumps(citations),
            commands_json=dumps(commands),
            metadata_json=dumps({
                "kind": action,
                "source_traceable": True,
                "micro_turn": True,
                "teaching_stage": "answer" if action == "ask" else (
                    "orient" if turn_index == 0 else "deepen"
                ),
                "knowledge_unit_index": turn_index,
                "board_action_count": len(board_actions),
                "standalone": True,
            }),
        )
        db.add(turn)
        await db.flush()
        return turn

    async def save_annotations(
        self,
        db: AsyncSession,
        session: TeachingStudioSession,
        annotations: list[dict[str, Any]],
    ) -> None:
        await db.execute(
            delete(TeachingStudioAnnotation).where(
                TeachingStudioAnnotation.session_id == session.id
            )
        )
        for value in annotations:
            db.add(
                TeachingStudioAnnotation(
                    session_id=session.id,
                    document_id=session.document_id,
                    page=value["page"],
                    author=value.get("author", "student"),
                    kind=value["kind"],
                    payload_json=dumps(value.get("payload", {})),
                )
            )
        await db.flush()


standalone_teaching_service = StandaloneTeachingService()
