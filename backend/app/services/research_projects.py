from __future__ import annotations

import asyncio
import base64
import difflib
import io
import json
import mimetypes
import os
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ModelEndpoint,
    ResearchExperiment,
    ResearchIdea,
    ResearchLiterature,
    ResearchManuscript,
    ResearchMemory,
    ResearchPresence,
    ResearchProject,
    ResearchProjectMember,
    ResearchReview,
    UserAccount,
)
from .common import dumps, loads
from .llm import DemoProvider, provider_from_endpoint
from .model_routing import latest_chat_endpoint
from .web_research import web_research_service


ROLE_LEVEL = {"viewer": 1, "reviewer": 2, "editor": 3, "manager": 4, "owner": 5}
MAX_LATEX_UPLOAD = 25_000_000
MAX_LATEX_PROJECT_SIZE = 60_000_000
MAX_LATEX_FILES = 300
TEXT_EXTENSIONS = {
    ".tex", ".bib", ".cls", ".sty", ".bst", ".bbx", ".cbx", ".cfg", ".def",
    ".clo", ".txt", ".md", ".csv", ".tsv",
}
ALLOWED_LATEX_EXTENSIONS = TEXT_EXTENSIONS | {
    ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg",
}


def model_row(model: Any) -> dict[str, Any]:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


class ResearchProjectService:
    @staticmethod
    def safe_project_path(raw_path: str) -> str:
        value = raw_path.replace("\\", "/").strip()
        path = PurePosixPath(value)
        if (
            not value
            or "\x00" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or re.match(r"^[A-Za-z]:", value)
        ):
            raise HTTPException(status_code=422, detail=f"LaTeX 项目包含不安全路径：{raw_path}")
        if path.suffix.lower() not in ALLOWED_LATEX_EXTENSIONS:
            raise HTTPException(status_code=422, detail=f"不支持导入文件类型：{path.suffix or path.name}")
        return path.as_posix()

    @staticmethod
    def _decode_text(data: bytes) -> str:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
        return data.decode("utf-8", errors="replace")

    def file_record(self, path: str, data: bytes) -> dict[str, Any]:
        extension = PurePosixPath(path).suffix.lower()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if extension in TEXT_EXTENSIONS:
            return {
                "content": self._decode_text(data),
                "encoding": "utf8",
                "mime": mime,
                "size": len(data),
            }
        return {
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
            "mime": mime,
            "size": len(data),
        }

    def normalize_files(self, files: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if not files or len(files) > MAX_LATEX_FILES:
            raise HTTPException(status_code=422, detail=f"LaTeX 项目须包含 1–{MAX_LATEX_FILES} 个文件")
        result: dict[str, dict[str, Any]] = {}
        total = 0
        for raw_path, raw_record in files.items():
            path = self.safe_project_path(raw_path)
            record = (
                raw_record.model_dump()
                if hasattr(raw_record, "model_dump")
                else dict(raw_record)
            )
            encoding = record.get("encoding", "utf8")
            content = str(record.get("content", ""))
            try:
                payload = content.encode("utf-8") if encoding == "utf8" else base64.b64decode(content, validate=True)
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f"文件 {path} 的编码无效") from exc
            total += len(payload)
            if total > MAX_LATEX_PROJECT_SIZE:
                raise HTTPException(status_code=413, detail="LaTeX 项目解压后超过 60 MB")
            result[path] = {
                "content": content,
                "encoding": encoding,
                "mime": str(record.get("mime") or mimetypes.guess_type(path)[0] or "application/octet-stream")[:200],
                "size": len(payload),
            }
        return result

    def manuscript_files(self, manuscript: ResearchManuscript | Any) -> dict[str, dict[str, Any]]:
        stored = loads(getattr(manuscript, "files_json", "{}"), {})
        if stored:
            return self.normalize_files(stored)
        main_file = getattr(manuscript, "main_file", "main.tex") or "main.tex"
        files = {
            main_file: self.file_record(main_file, (getattr(manuscript, "content", "") or "").encode("utf-8"))
        }
        bibliography = getattr(manuscript, "bibliography", "") or ""
        if bibliography:
            files["references.bib"] = self.file_record("references.bib", bibliography.encode("utf-8"))
        return files

    @staticmethod
    def detect_main_file(files: dict[str, dict[str, Any]], requested: str = "") -> str:
        if requested and requested in files and requested.lower().endswith(".tex"):
            return requested
        candidates = []
        for path, record in files.items():
            if not path.lower().endswith(".tex") or record.get("encoding") != "utf8":
                continue
            content = record.get("content", "")
            if "\\documentclass" in content and "\\begin{document}" in content:
                candidates.append(path)
        if not candidates:
            raise HTTPException(status_code=422, detail="未找到包含 \\documentclass 与 \\begin{document} 的主 .tex 文件")
        return sorted(candidates, key=lambda item: (item != "main.tex", item.count("/"), len(item)))[0]

    def import_latex_uploads(
        self, uploads: list[tuple[str, bytes]], requested_main: str = ""
    ) -> tuple[dict[str, dict[str, Any]], str]:
        if not uploads:
            raise HTTPException(status_code=422, detail="请选择 LaTeX 文件或项目压缩包")
        if sum(len(data) for _, data in uploads) > MAX_LATEX_UPLOAD:
            raise HTTPException(status_code=413, detail="上传文件总大小不能超过 25 MB")
        files: dict[str, dict[str, Any]] = {}
        if len(uploads) == 1 and uploads[0][0].lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(uploads[0][1])) as archive:
                    members = [item for item in archive.infolist() if not item.is_dir()]
                    if len(members) > MAX_LATEX_FILES:
                        raise HTTPException(status_code=422, detail=f"压缩包文件数超过 {MAX_LATEX_FILES}")
                    if sum(item.file_size for item in members) > MAX_LATEX_PROJECT_SIZE:
                        raise HTTPException(status_code=413, detail="压缩包解压后超过 60 MB")
                    raw_names = [item.filename.replace("\\", "/") for item in members]
                    for raw_name in raw_names:
                        raw_parts = PurePosixPath(raw_name).parts
                        if (
                            raw_name.startswith(("/", "\\"))
                            or ".." in raw_parts
                            or re.match(r"^[A-Za-z]:", raw_name)
                        ):
                            raise HTTPException(status_code=422, detail=f"LaTeX 项目包含不安全路径：{raw_name}")
                    roots = {PurePosixPath(name).parts[0] for name in raw_names if PurePosixPath(name).parts}
                    strip_root = len(roots) == 1 and roots.isdisjoint({".", ".."}) and all("/" in name for name in raw_names)
                    for member, raw_name in zip(members, raw_names, strict=True):
                        mode = member.external_attr >> 16
                        if mode and stat.S_ISLNK(mode):
                            raise HTTPException(status_code=422, detail="压缩包不能包含符号链接")
                        if strip_root:
                            raw_name = "/".join(PurePosixPath(raw_name).parts[1:])
                        path = self.safe_project_path(raw_name)
                        files[path] = self.file_record(path, archive.read(member))
            except zipfile.BadZipFile as exc:
                raise HTTPException(status_code=422, detail="ZIP 压缩包损坏或格式无效") from exc
        else:
            for raw_path, data in uploads:
                if raw_path.lower().endswith(".zip"):
                    raise HTTPException(status_code=422, detail="ZIP 项目不能与其他文件同时导入")
                path = self.safe_project_path(raw_path)
                files[path] = self.file_record(path, data)
        files = self.normalize_files(files)
        return files, self.detect_main_file(files, requested_main)

    def flatten_latex(
        self, files: dict[str, dict[str, Any]], main_file: str, depth: int = 0, visited: set[str] | None = None
    ) -> str:
        if depth > 20:
            return "% EvoAgent：include 深度超过限制\n"
        visited = visited or set()
        if main_file in visited:
            return f"% EvoAgent：跳过循环引用 {main_file}\n"
        visited.add(main_file)
        record = files.get(main_file, {})
        content = record.get("content", "") if record.get("encoding", "utf8") == "utf8" else ""
        parent = PurePosixPath(main_file).parent

        def replace(match: re.Match[str]) -> str:
            target = match.group(1).strip().replace("\\", "/")
            if not PurePosixPath(target).suffix:
                target += ".tex"
            candidate = (parent / target).as_posix()
            try:
                candidate = self.safe_project_path(candidate)
            except HTTPException:
                return match.group(0)
            if candidate not in files:
                return f"% EvoAgent：未找到 {candidate}\n"
            return self.flatten_latex(files, candidate, depth + 1, visited)

        return re.sub(r"\\(?:input|include)\s*\{([^}]+)\}", replace, content)

    def export_latex_zip(self, manuscript: ResearchManuscript) -> bytes:
        files = self.manuscript_files(manuscript)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, record in files.items():
                payload = (
                    record["content"].encode("utf-8")
                    if record.get("encoding") == "utf8"
                    else base64.b64decode(record["content"])
                )
                archive.writestr(path, payload)
        return buffer.getvalue()

    def version_diff(self, source: Any, manuscript: ResearchManuscript, file_path: str) -> str:
        old_files = self.manuscript_files(source)
        new_files = self.manuscript_files(manuscript)
        old = old_files.get(file_path, {}).get("content", "").splitlines()
        new = new_files.get(file_path, {}).get("content", "").splitlines()
        return "\n".join(difflib.unified_diff(old, new, fromfile=f"v{source.version}/{file_path}", tofile=f"v{manuscript.version}/{file_path}", lineterm=""))
    async def access(
        self,
        db: AsyncSession,
        project_id: str,
        user: UserAccount,
        minimum: str = "viewer",
    ) -> tuple[ResearchProject, str]:
        project = await db.get(ResearchProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="科研项目不存在")
        role = "owner" if project.owner_id == user.id else ""
        if not role:
            member = await db.scalar(
                select(ResearchProjectMember).where(
                    ResearchProjectMember.project_id == project_id,
                    ResearchProjectMember.user_id == user.id,
                    ResearchProjectMember.status == "active",
                )
            )
            role = member.role if member else ""
        if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum]:
            raise HTTPException(status_code=403, detail="当前项目角色无权执行此操作")
        return project, role

    async def project_payload(
        self, db: AsyncSession, project: ResearchProject, user: UserAccount
    ) -> dict[str, Any]:
        _, role = await self.access(db, project.id, user)
        counters = {}
        for name, model in (
            ("literature", ResearchLiterature),
            ("ideas", ResearchIdea),
            ("memories", ResearchMemory),
            ("experiments", ResearchExperiment),
            ("manuscripts", ResearchManuscript),
            ("reviews", ResearchReview),
        ):
            rows = (await db.scalars(select(model).where(model.project_id == project.id))).all()
            counters[name] = len(rows)
        data = model_row(project)
        data.update({
            "role": role,
            "counts": counters,
            "settings": loads(project.settings_json, {}),
        })
        return data

    async def context(self, db: AsyncSession, project: ResearchProject) -> str:
        memories = (
            await db.scalars(
                select(ResearchMemory)
                .where(ResearchMemory.project_id == project.id)
                .order_by(desc(ResearchMemory.locked), desc(ResearchMemory.updated_at))
                .limit(30)
            )
        ).all()
        ideas = (
            await db.scalars(
                select(ResearchIdea)
                .where(ResearchIdea.project_id == project.id)
                .order_by(desc(ResearchIdea.updated_at))
                .limit(12)
            )
        ).all()
        literature = (
            await db.scalars(
                select(ResearchLiterature)
                .where(
                    ResearchLiterature.project_id == project.id,
                    ResearchLiterature.status.in_(["included", "priority"]),
                )
                .order_by(desc(ResearchLiterature.credibility))
                .limit(20)
            )
        ).all()
        return (
            f"项目：{project.name}\n学科：{project.discipline}\n"
            f"研究问题：{project.research_question or project.description}\n"
            f"预期成果：{project.expected_outcome}\n\n"
            "项目记忆：\n"
            + "\n".join(f"- [{item.category}] {item.content}" for item in memories)
            + "\n\n已有 Idea：\n"
            + "\n".join(f"- {item.title}：{item.hypothesis}" for item in ideas)
            + "\n\n已纳入文献：\n"
            + "\n".join(
                f"- {item.title} ({item.year or '年份未知'}) DOI:{item.doi or '无'}"
                for item in literature
            )
        )

    async def chat(
        self,
        db: AsyncSession,
        *,
        system: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.35,
        max_output_tokens: int = 4000,
    ) -> str:
        endpoint: ModelEndpoint | None = await latest_chat_endpoint(db)
        if endpoint:
            provider = provider_from_endpoint(endpoint)
            model = endpoint.default_model
        else:
            provider = DemoProvider()
            model = "demo-model"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend((history or [])[-30:])
        messages.append({"role": "user", "content": user_message})
        response = await provider.chat(
            messages,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return response.content

    async def explore_idea(
        self,
        db: AsyncSession,
        project: ResearchProject,
        message: str,
        history: list[dict[str, str]],
    ) -> str:
        project_context = await self.context(db, project)
        return await self.chat(
            db,
            system=(
                "你是科研 Idea 苏格拉底式导师。每轮先回应用户，再只提出一个最有价值的"
                "澄清问题；检查新颖性、可证伪性、数据可得性、方法匹配和潜在反例。"
                "不要替用户虚构研究结论。引用项目文献时明确题名或 DOI。\n\n" + project_context
            ),
            user_message=message,
            history=history,
            temperature=0.45,
        )

    async def manuscript_assist(
        self,
        db: AsyncSession,
        project: ResearchProject,
        manuscript: ResearchManuscript,
        task: str,
        selection: str,
        instruction: str,
    ) -> str:
        labels = {
            "outline": "根据研究问题、已纳入文献和实验资产提出完整论文提纲",
            "polish": "在不改变事实含义和 LaTeX 命令的前提下润色所选文字",
            "logic": "检查论证链、章节衔接、过度结论和实验对主张的支撑",
            "citation_check": "检查需要引用、引用不足、引用键缺失和正文—文献表一致性",
            "academic_style": "改为严谨、克制、符合学术规范的表达，并解释主要修改",
            "translate": "进行中英学术互译，保持公式、LaTeX 命令和引用键不变",
            "response_letter": "根据稿件与已有审稿意见起草逐点回复信",
        }
        context = await self.context(db, project)
        target = selection.strip() or manuscript.content[:120000]
        return await self.chat(
            db,
            system=(
                "你是学术写作导师。不得编造数据、实验结果、作者、DOI 或引用。"
                "所有建议区分事实修改与表达修改；保留 LaTeX 结构。\n\n" + context
            ),
            user_message=(
                f"任务：{labels[task]}\n附加要求：{instruction or '无'}\n\n目标文本：\n{target}"
            ),
            temperature=0.25,
            max_output_tokens=8000,
        )

    async def academic_figure(self, db: AsyncSession, project: ResearchProject) -> dict[str, Any]:
        literature = (
            await db.scalars(
                select(ResearchLiterature)
                .where(
                    ResearchLiterature.project_id == project.id,
                    ResearchLiterature.status.in_(["included", "priority"]),
                )
                .order_by(ResearchLiterature.year)
                .limit(40)
            )
        ).all()
        if len(literature) < 2:
            raise HTTPException(status_code=422, detail="请先纳入至少两篇文献，再生成文献关联脉络")

        stopwords = {
            "the", "and", "for", "with", "from", "using", "based", "study", "research",
            "一种", "基于", "研究", "方法", "分析", "系统", "模型", "应用", "面向", "及其",
        }

        def tokens(item: ResearchLiterature) -> set[str]:
            text = f"{item.title} {item.abstract[:2400]} {' '.join(loads(item.tags_json, []))}".lower()
            words = re.findall(r"[a-z][a-z0-9+.#-]{2,}|[\u4e00-\u9fff]{2,6}", text)
            return {word for word in words if word not in stopwords}

        def authors(item: ResearchLiterature) -> set[str]:
            return {
                value.strip().casefold()
                for value in re.split(r"[,;；、]|\band\b", item.authors or "", flags=re.I)
                if len(value.strip()) > 1
            }

        node_tokens = {item.id: tokens(item) for item in literature}
        node_authors = {item.id: authors(item) for item in literature}
        nodes = [
            {
                "id": item.id,
                "label": item.title,
                "year": item.year,
                "type": "literature",
                "source_id": item.id,
                "authors": item.authors,
                "doi": item.doi,
                "url": item.url,
                "credibility": item.credibility,
                "status": item.status,
            }
            for item in literature
        ]
        candidates: list[dict[str, Any]] = []
        for left_index, left in enumerate(literature):
            for right in literature[left_index + 1 :]:
                shared_authors = sorted(node_authors[left.id] & node_authors[right.id])
                shared_terms = sorted(
                    node_tokens[left.id] & node_tokens[right.id],
                    key=lambda term: (-len(term), term),
                )[:5]
                union = node_tokens[left.id] | node_tokens[right.id]
                similarity = len(node_tokens[left.id] & node_tokens[right.id]) / max(1, len(union))
                year_gap = abs((left.year or 0) - (right.year or 0)) if left.year and right.year else None
                if shared_authors:
                    relation = "共同作者"
                    strength = min(1.0, 0.78 + 0.08 * len(shared_authors))
                    evidence = "共同作者：" + "、".join(shared_authors[:3])
                elif similarity >= 0.055 and len(shared_terms) >= 2:
                    relation = "主题/方法相似"
                    strength = min(0.92, 0.38 + similarity * 3.2)
                    evidence = "共享主题词：" + "、".join(shared_terms)
                elif year_gap is not None and year_gap <= 2 and len(shared_terms) >= 1:
                    relation = "同期研究主题"
                    strength = min(0.68, 0.34 + len(shared_terms) * 0.07)
                    evidence = f"年份相差 {year_gap} 年；共享：" + "、".join(shared_terms)
                else:
                    continue
                candidates.append({
                    "source": left.id,
                    "target": right.id,
                    "label": relation,
                    "relation": relation,
                    "strength": round(strength, 2),
                    "evidence": evidence,
                })
        candidates.sort(key=lambda edge: edge["strength"], reverse=True)
        edges = candidates[: max(len(literature) * 3, 12)]
        connected = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
        # Keep isolated references visible and connect them by the weakest defensible time relation.
        for item in literature:
            if item.id in connected or len(literature) < 2:
                continue
            neighbours = [other for other in literature if other.id != item.id and item.year and other.year]
            if not neighbours:
                continue
            neighbour = min(neighbours, key=lambda other: abs(item.year - other.year))
            gap = abs(item.year - neighbour.year)
            edges.append({
                "source": item.id,
                "target": neighbour.id,
                "label": "时间邻近",
                "relation": "时间邻近",
                "strength": round(max(0.18, 0.42 - gap * 0.04), 2),
                "evidence": f"发表年份相差 {gap} 年；仅表示时间脉络，不代表引用关系",
            })
        return {
            "title": f"{project.name}参考文献关联网络",
            "subtitle": "关系由共同作者、题名/摘要主题词和发表时间计算；不把相似性伪装为直接引用",
            "style": "academic",
            "background": "#ffffff",
            "foreground": "#111111",
            "schema_version": "2.0",
            "nodes": nodes,
            "edges": edges,
            "source_ids": [item.id for item in literature],
            "metrics": {
                "literature_count": len(nodes),
                "relation_count": len(edges),
                "strong_relations": sum(edge["strength"] >= 0.7 for edge in edges),
            },
        }

    async def search_literature(
        self,
        db: AsyncSession,
        project: ResearchProject,
        user: UserAccount,
        query: str,
        target_count: int,
        year_from: int | None,
        year_to: int | None,
    ) -> list[ResearchLiterature]:
        period = ""
        if year_from or year_to:
            period = f"，年份范围 {year_from or 1800}-{year_to or datetime.now().year}"
        task = f"学术调研：{query}。目标约 {target_count} 篇真实文献{period}"

        async def ignore_event(_event: dict[str, Any]) -> None:
            return None

        sources = await web_research_service.collect(task, ignore_event)
        results: list[ResearchLiterature] = []
        existing = {
            (item.doi or item.url or item.title).strip().casefold()
            for item in (
                await db.scalars(
                    select(ResearchLiterature).where(ResearchLiterature.project_id == project.id)
                )
            ).all()
        }
        for source in sources[:target_count]:
            key = (
                str(source.get("doi") or source.get("url") or source.get("title") or "")
                .strip()
                .casefold()
            )
            if not key or key in existing:
                continue
            year = source.get("published_year")
            if year_from and year and int(year) < year_from:
                continue
            if year_to and year and int(year) > year_to:
                continue
            credibility = source.get("credibility") or {}
            item = ResearchLiterature(
                project_id=project.id,
                created_by=user.id,
                title=str(source.get("title") or "未命名文献")[:2000],
                authors=str(source.get("authors") or source.get("author") or "")[:2000],
                year=int(year) if year else None,
                doi=str(source.get("doi") or "")[:300],
                url=str(source.get("url") or ""),
                source=str(source.get("source") or "Web")[:120],
                abstract=str(source.get("description") or source.get("content") or "")[:50000],
                credibility=int(credibility.get("score") or 50),
                metadata_json=dumps({"credibility": credibility, "status": source.get("status")}),
            )
            db.add(item)
            results.append(item)
            existing.add(key)
        await db.flush()
        return results

    def latex_preview(self, content: str) -> dict[str, Any]:
        title = re.search(r"\\title\{([^}]*)\}", content)
        abstract = re.search(r"\\begin\{abstract\}([\s\S]*?)\\end\{abstract\}", content)
        authors = [
            re.sub(r"\\corref\{[^}]*\}", "", item).strip()
            for item in re.findall(r"\\author(?:\[[^\]]*\])?\{([^}]*)\}", content)
        ]
        keyword_match = re.search(r"\\begin\{keyword\}([\s\S]*?)\\end\{keyword\}", content)
        keywords = (
            [item.strip() for item in re.split(r"\\sep|[,;；]", keyword_match.group(1)) if item.strip()]
            if keyword_match
            else []
        )
        sections = []
        pattern = re.compile(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}")
        matches = list(pattern.finditer(content))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = content[match.end() : end]
            body = re.sub(r"%.*", "", body)
            sections.append(
                {"level": match.group(1), "title": match.group(2), "content": body.strip()}
            )
        equations = re.findall(r"\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]", content)
        citations = sorted(set(re.findall(r"\\cite\{([^}]*)\}", content)))
        return {
            "title": title.group(1) if title else "未命名论文",
            "authors": authors,
            "abstract": abstract.group(1).strip() if abstract else "",
            "keywords": keywords,
            "sections": sections,
            "equations": [left or right for left, right in equations],
            "citations": citations,
            "warnings": self.latex_warnings(content),
        }

    @staticmethod
    def latex_warnings(content: str) -> list[str]:
        warnings = []
        for environment in ("document", "abstract", "figure", "table", "equation"):
            if content.count(f"\\begin{{{environment}}}") != content.count(
                f"\\end{{{environment}}}"
            ):
                warnings.append(f"{environment} 环境起止数量不一致")
        if content.count("{") != content.count("}"):
            warnings.append("花括号数量不一致")
        if "\\begin{document}" not in content:
            warnings.append("缺少 \\begin{document}")
        return warnings

    async def compile_latex(self, manuscript: ResearchManuscript) -> tuple[bytes, str]:
        blocked_patterns = {
            r"\\(?:write18|immediate\s*\\write18)\b": r"\write18",
            r"\\(?:input|include)\s*\{?\s*(?:/|[A-Za-z]:|\.\.)": "外部路径 input/include",
            r"\\(?:openin|openout|read|write)\b[\s\S]{0,80}(?:/|[A-Za-z]:|\.\.)": "外部文件访问",
            r"\\catcode\b": r"\catcode",
        }
        files = self.manuscript_files(manuscript)
        main_file = self.detect_main_file(files, manuscript.main_file)
        for file_path, record in files.items():
            if record.get("encoding") != "utf8":
                continue
            for pattern, label in blocked_patterns.items():
                if re.search(pattern, record.get("content", ""), re.IGNORECASE):
                    raise HTTPException(
                        status_code=422,
                        detail=f"LaTeX 安全检查未通过：{file_path} 禁止使用 {label}。",
                    )
        bundled_tectonic = os.environ.get("EVO_BUNDLED_TECTONIC", "").strip()
        engine = next(
            (
                item
                for item in (
                    "xelatex",
                    "pdflatex",
                    "tectonic",
                    bundled_tectonic if bundled_tectonic and Path(bundled_tectonic).is_file() else "",
                )
                if item and (Path(item).is_file() or shutil.which(item))
            ),
            None,
        )
        if not engine:
            raise HTTPException(
                status_code=409,
                detail="未检测到本机 LaTeX 引擎。请安装 Tectonic、TeX Live 或 MiKTeX；浏览器即时预览仍可使用。",
            )
        with tempfile.TemporaryDirectory(prefix="evoagent-latex-") as folder:
            root = Path(folder)
            for file_path, record in files.items():
                destination = (root / Path(*PurePosixPath(file_path).parts)).resolve()
                if root.resolve() not in destination.parents:
                    raise HTTPException(status_code=422, detail="LaTeX 项目路径越界")
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = (
                    record["content"].encode("utf-8")
                    if record.get("encoding") == "utf8"
                    else base64.b64decode(record["content"])
                )
                destination.write_bytes(payload)
            tex = root / Path(*PurePosixPath(main_file).parts)
            if Path(engine).name.lower() == "tectonic.exe" or engine == "tectonic":
                cache_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "EvoAgent" / "latex-cache"
                cache_root.mkdir(parents=True, exist_ok=True)
                command = [
                    engine,
                    "--keep-logs",
                    "--untrusted",
                    "--outdir",
                    str(root),
                    str(tex),
                ]
            else:
                command = [
                    engine,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(root),
                    str(tex),
                ]
            process_env = os.environ.copy()
            process_env["TECTONIC_CACHE_DIR"] = str(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "EvoAgent" / "latex-cache")
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=root,
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise HTTPException(
                    status_code=504,
                    detail="LaTeX 编译超过 120 秒。内置 Tectonic 首次使用需联网下载标准宏包缓存；请检查网络后重试，缓存完成后可离线使用。",
                ) from exc
            pdf = root / f"{tex.stem}.pdf"
            if process.returncode or not pdf.exists():
                log = stdout.decode("utf-8", errors="replace")[-5000:]
                raise HTTPException(status_code=422, detail=f"LaTeX 编译失败：{log}")
            return pdf.read_bytes(), Path(engine).name

    async def generate_review(
        self,
        db: AsyncSession,
        project: ResearchProject,
        manuscript: ResearchManuscript,
        roles: list[str],
        venue: str = "通用学术期刊/会议",
        rigor: str = "strict",
        focus: str = "",
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, str, dict[str, float], list[dict[str, Any]], dict[str, Any]]:
        role_specs = {
            "domain": ("领域专家", "研究问题的重要性、相关工作覆盖、学科贡献与新颖性"),
            "method": ("方法学审稿人", "研究设计、假设、变量、对照、公平比较与因果论证"),
            "experiment": ("实验审稿人", "数据集、基线、消融、误差分析、复现实验与资源披露"),
            "statistics": ("统计审稿人", "样本量、统计检验、效应量、置信区间、多重比较与不确定性"),
            "writing": ("写作规范审稿人", "结构、表述、图表、引用、LaTeX 与学术写作规范"),
            "strict": ("严格反方审稿人", "主动寻找反例、过度主张、伦理风险、失败模式与拒稿级缺陷"),
        }
        selected_roles = list(dict.fromkeys(roles or ["domain", "method", "writing"]))
        selected_roles = [role for role in selected_roles if role in role_specs][:6]
        files = self.manuscript_files(manuscript)
        content = self.flatten_latex(files, manuscript.main_file)[:140000]
        context = await self.context(db, project)

        feature_checks = {
            "has_abstract": bool(re.search(r"\\begin\{abstract\}[\s\S]+?\\end\{abstract\}", content)),
            "has_related_work": bool(re.search(r"\\section\{[^}]*(相关|Related)", content, re.I)),
            "has_method": bool(re.search(r"\\section\{[^}]*(方法|Method|Approach)", content, re.I)),
            "has_experiment": bool(re.search(r"\\section\{[^}]*(实验|Experiment|Evaluation)", content, re.I)),
            "has_limitations": bool(re.search(r"limitations?|局限|威胁", content, re.I)),
            "has_seed": bool(re.search(r"random\s*seed|随机种子", content, re.I)),
            "has_statistics": bool(re.search(r"p\s*[<=>]|置信区间|confidence interval|effect size|效应量", content, re.I)),
            "has_sample_size": bool(re.search(r"(?:n|样本量)\s*[=:为]\s*\d+", content, re.I)),
            "has_baseline": bool(re.search(r"baseline|基线|对照组", content, re.I)),
            "has_ablation": bool(re.search(r"ablation|消融", content, re.I)),
            "has_data_code": bool(re.search(r"data|dataset|数据集|代码|repository|github", content, re.I)),
            "has_ethics": bool(re.search(r"ethics|伦理|隐私|consent|知情同意", content, re.I)),
            "has_citations": bool(re.search(r"\\cite\w*\{[^}]+\}", content)),
        }
        section_count = len(re.findall(r"\\section\{", content))
        citation_count = len(set(re.findall(r"\\cite\w*\{([^}]+)\}", content)))

        def clamp_score(value: Any, default: float = 5.0) -> float:
            try:
                return round(max(0.0, min(10.0, float(value))), 2)
            except (TypeError, ValueError):
                return default

        def extract_json(value: str) -> dict[str, Any] | None:
            decoder = json.JSONDecoder()
            for index, char in enumerate(value):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(value[index:])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
            return None

        def fallback_report(role: str) -> dict[str, Any]:
            base = 4.2 + min(section_count, 7) * 0.35 + min(citation_count, 8) * 0.08
            scores = {
                "novelty": base + (0.8 if feature_checks["has_related_work"] else -0.5),
                "correctness": base + (0.7 if feature_checks["has_method"] else -1.0),
                "reproducibility": base + sum(
                    0.45 for key in ("has_seed", "has_sample_size", "has_baseline", "has_data_code") if feature_checks[key]
                ) - (0.8 if not feature_checks["has_experiment"] else 0),
                "significance": base + (0.5 if feature_checks["has_experiment"] else -0.5),
                "clarity": base + (0.6 if feature_checks["has_abstract"] else -0.8),
            }
            issues: list[dict[str, Any]] = []
            role_missing = {
                "domain": [("相关工作", "has_related_work", "相关工作与创新定位不足", "补充代表性工作并逐项说明差异与贡献边界")],
                "method": [("研究方法", "has_method", "方法定义与研究设计披露不足", "明确假设、变量、对照条件、流程与有效性威胁")],
                "experiment": [("实验设置", "has_seed", "缺少随机种子或重复试验设置", "披露随机种子、运行次数、软硬件环境与超参数")],
                "statistics": [("统计分析", "has_statistics", "未发现统计显著性、效应量或置信区间报告", "同时报告效应量、置信区间、检验前提与多重比较校正")],
                "writing": [("摘要与结构", "has_abstract", "摘要或论文结构不完整", "按问题—方法—结果—贡献重写摘要并统一术语")],
                "strict": [("局限性", "has_limitations", "缺少局限性、失败案例与外部有效性讨论", "增加局限性章节并给出反例、失败模式和适用边界")],
            }
            for category, key, issue, suggestion in role_missing.get(role, []):
                if not feature_checks[key]:
                    issues.append({
                        "category": category,
                        "severity": "major",
                        "location": "对应章节",
                        "issue": issue,
                        "evidence": f"自动结构检查未检出 {key} 对应的可核验表述",
                        "suggestion": suggestion,
                    })
            if not issues:
                issues.append({
                    "category": "证据完整性",
                    "severity": "minor",
                    "location": "全文",
                    "issue": "仍需逐项人工核验结论是否均由实验、引文或推导直接支持",
                    "evidence": "自动审稿只能检查已提供稿件，不能核验未提供的原始数据与外部事实",
                    "suggestion": "建立主张—证据矩阵，并由作者和独立复核者签认",
                })
            return {
                "summary": f"{role_specs[role][0]}完成了结构化审阅；当前稿件包含 {section_count} 个一级章节和 {citation_count} 组引用键。",
                "strengths": ["稿件已形成可审阅的 LaTeX 结构", "研究内容可通过版本与文件来源继续核验"],
                "weaknesses": [entry["issue"] for entry in issues],
                "scores": {key: clamp_score(value) for key, value in scores.items()},
                "confidence": 0.72,
                "decision": "major_revision" if any(item["severity"] == "major" for item in issues) else "minor_revision",
                "issues": issues,
                "checklist": [
                    {"item": key, "passed": passed, "evidence": "稿件文本检出" if passed else "稿件文本未检出"}
                    for key, passed in feature_checks.items()
                ],
                "mode": "deterministic_fallback",
            }

        reviewer_reports: list[dict[str, Any]] = []
        for role in selected_roles:
            role_name, rubric = role_specs[role]
            if on_progress:
                await on_progress({
                    "type": "reviewer_started",
                    "role": role,
                    "role_name": role_name,
                    "completed": len(reviewer_reports),
                    "total": len(selected_roles),
                })
            prompt = (
                f"你是独立的{role_name}，投稿目标为“{venue}”，严格度为 {rigor}。仅从本稿件与项目上下文判断，"
                f"重点审查：{rubric}。额外关注：{focus or '无'}。不得假装读取原始数据或访问未提供来源。\n"
                "只输出一个 JSON 对象，字段为 summary、strengths(字符串数组)、weaknesses(字符串数组)、"
                "scores(含 novelty/correctness/reproducibility/significance/clarity，均为0到10)、confidence(0到1)、"
                "decision(accept/minor_revision/major_revision/reject)、issues(数组，每项含 category/severity/location/issue/evidence/suggestion)、"
                "checklist(数组，每项含 item/passed/evidence)。\n\n项目上下文：\n"
                + context[:30000]
                + "\n\n论文：\n"
                + content
            )
            raw = await self.chat(
                db,
                system=f"你是与其他委员隔离、不会被多数意见影响的{role_name}。输出必须可量化、可复核。",
                user_message=prompt,
                temperature=0.1,
                max_output_tokens=4200,
            )
            parsed = extract_json(raw) or fallback_report(role)
            fallback = fallback_report(role)
            raw_scores = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
            normalized = {
                "role": role,
                "role_name": role_name,
                "rubric": rubric,
                "summary": str(parsed.get("summary") or fallback["summary"]),
                "strengths": [str(item) for item in parsed.get("strengths", fallback["strengths"])][:8],
                "weaknesses": [str(item) for item in parsed.get("weaknesses", fallback["weaknesses"])][:8],
                "scores": {key: clamp_score(raw_scores.get(key), fallback["scores"][key]) for key in fallback["scores"]},
                "confidence": round(
                    max(
                        0.0,
                        min(
                            1.0,
                            float(parsed.get("confidence", fallback["confidence"]))
                            if isinstance(parsed.get("confidence", fallback["confidence"]), (int, float))
                            else fallback["confidence"],
                        ),
                    ),
                    2,
                ),
                "decision": parsed.get("decision") if parsed.get("decision") in {"accept", "minor_revision", "major_revision", "reject"} else fallback["decision"],
                "issues": parsed.get("issues") if isinstance(parsed.get("issues"), list) else fallback["issues"],
                "checklist": parsed.get("checklist") if isinstance(parsed.get("checklist"), list) else fallback["checklist"],
                "mode": parsed.get("mode", "llm_structured"),
            }
            reviewer_reports.append(normalized)
            if on_progress:
                await on_progress({
                    "type": "reviewer_completed",
                    "role": role,
                    "role_name": role_name,
                    "completed": len(reviewer_reports),
                    "total": len(selected_roles),
                    "decision": normalized["decision"],
                    "confidence": normalized["confidence"],
                    "scores": normalized["scores"],
                })

        score_keys = ["novelty", "correctness", "reproducibility", "significance", "clarity"]
        scores = {
            key: round(sum(report["scores"][key] for report in reviewer_reports) / len(reviewer_reports) * 10, 1)
            for key in score_keys
        }
        issues: list[dict[str, Any]] = []
        for report in reviewer_reports:
            for raw_issue in report["issues"][:10]:
                if not isinstance(raw_issue, dict) or not raw_issue.get("issue"):
                    continue
                issues.append({
                    "category": str(raw_issue.get("category", "综合评议"))[:60],
                    "reviewer_role": report["role"],
                    "severity": raw_issue.get("severity") if raw_issue.get("severity") in {"major", "minor"} else "major",
                    "location": str(raw_issue.get("location", "全文"))[:240],
                    "issue": str(raw_issue["issue"]),
                    "evidence": str(raw_issue.get("evidence", "")),
                    "suggestion": str(raw_issue.get("suggestion", "")),
                    "confidence": report["confidence"],
                })
        major_count = sum(item["severity"] == "major" for item in issues)
        mean_score = sum(scores.values()) / len(scores)
        reject_votes = sum(report["decision"] == "reject" for report in reviewer_reports)
        threshold = 78 if rigor == "top_venue" else 70 if rigor == "strict" else 64
        if reject_votes >= max(1, len(reviewer_reports) // 2) or scores["correctness"] < 45:
            decision = "reject"
        elif mean_score >= threshold + 10 and major_count == 0:
            decision = "accept"
        elif mean_score >= threshold and major_count <= 1:
            decision = "minor_revision"
        else:
            decision = "major_revision"
        disagreement = round(
            sum(
                max(report["scores"][key] for report in reviewer_reports)
                - min(report["scores"][key] for report in reviewer_reports)
                for key in score_keys
            )
            / len(score_keys)
            * 10,
            1,
        )
        summary = (
            f"模拟审稿委员会完成 {len(reviewer_reports)} 份相互独立的评议。综合得分 {mean_score:.1f}/100，"
            f"主要问题 {major_count} 项，委员平均分歧 {disagreement:.1f} 分，建议结论：{decision}。"
        )
        report = {
            "schema_version": "1.0",
            "venue": venue,
            "rigor": rigor,
            "focus": focus,
            "manuscript_version": manuscript.version,
            "main_file": manuscript.main_file,
            "reviewer_reports": reviewer_reports,
            "committee": {
                "summary": summary,
                "decision": decision,
                "mean_score": round(mean_score, 1),
                "major_issues": major_count,
                "minor_issues": len(issues) - major_count,
                "disagreement": disagreement,
                "decision_threshold": threshold,
                "feature_checks": feature_checks,
            },
            "methodology": {
                "independent_reviews": True,
                "score_scale": "0-100 (委员0-10分的等权平均)",
                "limitations": "模拟审稿不替代真实同行评议；未提供的原始数据、外部链接与实验结果均未被验证。",
            },
        }
        if on_progress:
            await on_progress({
                "type": "committee_completed",
                "completed": len(reviewer_reports),
                "total": len(selected_roles),
                "decision": decision,
                "scores": scores,
            })
        return summary, decision, scores, issues, report

    async def active_presence(self, db: AsyncSession, project_id: str) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
        rows = (
            await db.scalars(
                select(ResearchPresence)
                .where(
                    ResearchPresence.project_id == project_id,
                    ResearchPresence.updated_at >= cutoff,
                )
                .order_by(desc(ResearchPresence.updated_at))
            )
        ).all()
        users = (
            {
                item.id: item
                for item in (
                    await db.scalars(
                        select(UserAccount).where(UserAccount.id.in_({row.user_id for row in rows}))
                    )
                ).all()
            }
            if rows
            else {}
        )
        return [
            {
                **model_row(item),
                "display_name": users.get(item.user_id).display_name
                if users.get(item.user_id)
                else "已离线成员",
                "cursor": loads(item.cursor_json, {}),
            }
            for item in rows
        ]


research_project_service = ResearchProjectService()
