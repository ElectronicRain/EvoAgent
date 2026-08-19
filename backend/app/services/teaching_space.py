from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    AgentDefinition,
    LearningProject,
    TeachingAnnotation,
    TeachingDocument,
    TeachingSession,
    TeachingTurn,
    UserAccount,
)
from .common import dumps, loads
from .knowledge_processing import extract_sections
from .learning_space import learning_space_service


SUPPORTED_SUFFIXES = {".pdf", ".pptx"}


class TeachingSpaceService:
    @property
    def root(self) -> Path:
        path = settings.workspace_root / "teaching"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def document_access(
        self, db: AsyncSession, document_id: str, user: UserAccount
    ) -> TeachingDocument:
        item = await db.get(TeachingDocument, document_id)
        if not item:
            raise LookupError("教学文档不存在")
        if item.owner_id != user.id:
            raise PermissionError("无权访问该教学文档")
        return item

    async def session_access(
        self, db: AsyncSession, session_id: str, user: UserAccount
    ) -> TeachingSession:
        item = await db.get(TeachingSession, session_id)
        if not item:
            raise LookupError("教学会话不存在")
        if item.owner_id != user.id:
            raise PermissionError("无权访问该教学会话")
        return item

    @staticmethod
    def document_payload(item: TeachingDocument) -> dict[str, Any]:
        return {
            "id": item.id,
            "project_id": item.project_id,
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
    def turn_payload(item: TeachingTurn) -> dict[str, Any]:
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

    async def session_payload(self, db: AsyncSession, item: TeachingSession) -> dict[str, Any]:
        document = await db.get(TeachingDocument, item.document_id)
        turns = (
            await db.scalars(
                select(TeachingTurn)
                .where(TeachingTurn.session_id == item.id)
                .order_by(TeachingTurn.created_at)
            )
        ).all()
        annotations = (
            await db.scalars(
                select(TeachingAnnotation)
                .where(TeachingAnnotation.session_id == item.id)
                .order_by(TeachingAnnotation.created_at)
            )
        ).all()
        agent = await db.get(AgentDefinition, item.agent_id) if item.agent_id else None
        return {
            "id": item.id,
            "project_id": item.project_id,
            "document_id": item.document_id,
            "agent_id": item.agent_id,
            "agent": {"id": agent.id, "name": agent.name, "description": agent.description} if agent else None,
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

    @staticmethod
    def _libreoffice() -> str | None:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if executable:
            return executable
        for candidate in (
            Path("C:/Program Files/LibreOffice/program/soffice.exe"),
            Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    def _convert_pptx(self, source: Path, destination_dir: Path) -> tuple[Path | None, str]:
        executable = self._libreoffice()
        if not executable:
            return None, "未发现本地课件渲染运行时，已启用结构化幻灯片预览"
        try:
            completed = subprocess.run(
                [executable, "--headless", "--convert-to", "pdf", "--outdir", str(destination_dir), str(source)],
                capture_output=True,
                check=False,
                timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = destination_dir / f"{source.stem}.pdf"
            if completed.returncode == 0 and output.is_file():
                return output, "PPTX 已转换为静态 PDF；动画和触发效果不会保留"
            detail = (completed.stderr or completed.stdout).decode(errors="replace")[:300]
            return None, f"课件转换失败，已启用结构化幻灯片预览：{detail}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"课件转换失败，已启用结构化幻灯片预览：{str(exc)[:180]}"

    async def store_document(
        self,
        db: AsyncSession,
        project: LearningProject,
        user: UserAccount,
        filename: str,
        data: bytes,
    ) -> TeachingDocument:
        safe_name = Path(filename.replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("智能讲解教室仅支持 PDF 和 PPTX")
        digest = hashlib.sha256(data).hexdigest()
        existing = await db.scalar(
            select(TeachingDocument).where(
                TeachingDocument.project_id == project.id,
                TeachingDocument.owner_id == user.id,
                TeachingDocument.file_hash == digest,
            )
        )
        if existing:
            return existing
        sections, mime = extract_sections(safe_name, data)
        if not sections:
            raise ValueError("文档中没有可分析的页面")
        folder = self.root / project.id / digest[:16]
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / f"source{suffix}"
        source.write_bytes(data)
        rendered: Path | None = source if suffix == ".pdf" else None
        conversion_note = "PDF 原文件直接渲染"
        if suffix == ".pptx":
            rendered, conversion_note = self._convert_pptx(source, folder)
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
        item = TeachingDocument(
            project_id=project.id,
            owner_id=user.id,
            title=Path(safe_name).stem[:240],
            filename=safe_name[:500],
            mime_type=mime,
            source_path=str(source),
            rendered_path=str(rendered) if rendered else None,
            file_hash=digest,
            page_count=page_count,
            sections_json=dumps(section_payload),
            metadata_json=dumps({"conversion_note": conversion_note, "source_kind": suffix.removeprefix(".")}),
            status="ready" if rendered or suffix == ".pptx" else "failed",
        )
        db.add(item)
        await db.flush()
        return item

    @staticmethod
    def create_lesson_plan(document: TeachingDocument, project: LearningProject) -> list[dict[str, Any]]:
        sections = loads(document.sections_json, [])
        return [
            {
                "unit": index,
                "page": int(section.get("page", index)),
                "title": str(section.get("title") or f"第 {index} 页")[:160],
                "summary": re.sub(r"\s+", " ", str(section.get("text", "")))[:160],
                "estimated_minutes": 2 if len(str(section.get("text", ""))) < 500 else 4,
                "target": project.target[:300],
                "status": "pending",
            }
            for index, section in enumerate(sections, 1)
        ]

    @staticmethod
    def _micro_turn(text: str, limit: int = 180) -> str:
        clean = re.sub(r"\n{3,}", "\n\n", text.strip())
        if len(clean) <= limit:
            return clean
        candidates = list(re.finditer(r"[。！？!?；;]\s*", clean[: limit + 1]))
        cut = candidates[-1].end() if candidates and candidates[-1].end() >= 60 else limit
        return clean[:cut].rstrip() + ("" if cut < len(clean) and clean[cut - 1] in "。！？!?；;" else "……")

    @staticmethod
    def _page_section(document: TeachingDocument, page: int) -> dict[str, Any]:
        sections = loads(document.sections_json, [])
        if not sections:
            return {"page": 1, "title": "当前页面", "text": ""}
        return sections[min(max(page, 1), len(sections)) - 1]

    async def create_session(
        self,
        db: AsyncSession,
        project: LearningProject,
        document: TeachingDocument,
        user: UserAccount,
        settings_data: dict[str, Any],
        agent_id: str | None,
    ) -> TeachingSession:
        selected_agent = agent_id or loads(project.agent_bindings_json, {}).get("tutor") or None
        session = TeachingSession(
            project_id=project.id,
            document_id=document.id,
            owner_id=user.id,
            agent_id=selected_agent,
            status="ready",
            current_page=1,
            settings_json=dumps(settings_data),
            lesson_plan_json=dumps(self.create_lesson_plan(document, project)),
        )
        db.add(session)
        await db.flush()
        greeting = TeachingTurn(
            session_id=session.id,
            role="assistant",
            page=1,
            content=self._micro_turn(
                f"课件《{document.title}》已经准备好。我会围绕你的目标“{project.target or project.name}”逐页讲解，每次只推进一个要点。你可以随时提问、暂停或停止。准备好后点击开始。"
            ),
            metadata_json=dumps({"kind": "greeting", "source_traceable": True}),
        )
        db.add(greeting)
        await db.flush()
        return session

    async def teach(
        self,
        db: AsyncSession,
        session: TeachingSession,
        project: LearningProject,
        user: UserAccount,
        *,
        message: str,
        action: str,
        page: int,
    ) -> TeachingTurn:
        document = await db.get(TeachingDocument, session.document_id)
        if not document:
            raise LookupError("教学文档不存在")
        page = min(max(1, page), max(1, document.page_count))
        session.current_page = page
        session.current_unit = page
        session.status = "explaining"
        session.progress = min(99, round(100 * (page - 1) / max(1, document.page_count)))
        section = self._page_section(document, page)
        if action == "ask" and message.strip():
            user_turn = TeachingTurn(
                session_id=session.id, role="user", page=page, content=message.strip()
            )
            db.add(user_turn)
            await db.flush()
        intent = message.strip() if message.strip() else "继续讲解当前页面的下一个核心要点"
        teaching_prompt = (
            f"你正在一对一讲解课件《{document.title}》第{page}页。页面标题：{section.get('title')}。"
            f"页面内容：{str(section.get('text', ''))[:5000]}\n"
            f"学生当前请求：{intent}\n"
            "请只讲一个最小知识点，使用60至120个汉字，最多180字；先回应当前问题，再给出一步解释或一个小例子；"
            "不要输出整章提纲，不要连续提出多个问题。最后用一句简短确认问题收尾。"
        )
        reply = await learning_space_service.tutor(
            db,
            project,
            user,
            message=teaching_prompt,
            mode="explain",
            knowledge_node_id=None,
            agent_id=session.agent_id,
        )
        content = self._micro_turn(reply.content)
        focus = re.sub(r"\s+", " ", str(section.get("text", ""))).strip()[:28]
        commands = []
        if focus:
            commands.append({"type": "focus_text", "page": page, "text": focus, "color": "#1769c2"})
        turn = TeachingTurn(
            session_id=session.id,
            role="assistant",
            page=page,
            content=content,
            citations_json=reply.citations_json,
            commands_json=dumps(commands),
            metadata_json=dumps({
                "kind": action,
                "source_traceable": True,
                "learning_target": project.target,
                "micro_turn": True,
            }),
        )
        db.add(turn)
        await db.flush()
        return turn

    async def save_annotations(
        self,
        db: AsyncSession,
        session: TeachingSession,
        annotations: list[dict[str, Any]],
    ) -> None:
        await db.execute(delete(TeachingAnnotation).where(TeachingAnnotation.session_id == session.id))
        for value in annotations:
            db.add(
                TeachingAnnotation(
                    session_id=session.id,
                    document_id=session.document_id,
                    page=value["page"],
                    author=value.get("author", "student"),
                    kind=value["kind"],
                    payload_json=dumps(value.get("payload", {})),
                )
            )
        await db.flush()


teaching_space_service = TeachingSpaceService()
