from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document
from pptx import Presentation
from pypdf import PdfReader


@dataclass
class ExtractedSection:
    text: str
    heading: str = ""
    locator: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkDraft:
    content: str
    level: str
    parent_index: int | None
    metadata: dict[str, Any]


class MainTextHTMLParser(HTMLParser):
    """Small dependency-free HTML extractor that removes navigation and executable content."""

    ignored = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    blocks = {"p", "div", "article", "section", "main", "li", "br", "tr", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in self.ignored:
            self.depth += 1
        if tag == "title":
            self._in_title = True
        if not self.depth and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self.ignored and self.depth:
            self.depth -= 1
        if not self.depth and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.depth:
            return
        if self._in_title:
            self.title += data
        self.parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(value: str) -> tuple[str, str]:
    parser = MainTextHTMLParser()
    parser.feed(value)
    return parser.text, clean_text(parser.title)[0]


def clean_text(value: str) -> tuple[str, dict[str, int]]:
    """Normalize Unicode, remove control/noise lines, and collapse repeated lines."""

    original_length = len(value)
    value = unicodedata.normalize("NFKC", value.replace("\u00a0", " "))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    raw_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    counts = Counter(line for line in raw_lines if 0 < len(line) <= 120)
    seen: Counter[str] = Counter()
    result: list[str] = []
    removed_repeated = 0
    removed_noise = 0
    for line in raw_lines:
        if not line:
            if result and result[-1]:
                result.append("")
            continue
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"(?:[-_=*•·.。]{3,}|\d{1,4})", compact):
            removed_noise += 1
            continue
        # Repeated short headers/footers are retained once, then treated as layout noise.
        if counts[line] >= 3:
            seen[line] += 1
            if seen[line] > 1:
                removed_repeated += 1
                continue
        result.append(line)
    cleaned = "\n".join(result)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, {
        "original_chars": original_length,
        "cleaned_chars": len(cleaned),
        "noise_lines_removed": removed_noise,
        "repeated_lines_removed": removed_repeated,
    }


def extract_sections(filename: str, data: bytes) -> tuple[list[ExtractedSection], str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        sections = [
            ExtractedSection(
                text=page.extract_text() or "",
                locator=f"第 {index} 页",
                metadata={"page": index},
            )
            for index, page in enumerate(reader.pages, 1)
        ]
        return sections, "application/pdf"
    if suffix == ".docx":
        document = Document(io.BytesIO(data))
        sections: list[ExtractedSection] = []
        heading = ""
        buffer: list[str] = []
        section_index = 1
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = (paragraph.style.name if paragraph.style else "").lower()
            if "heading" in style_name or "标题" in style_name:
                if buffer:
                    sections.append(
                        ExtractedSection("\n".join(buffer), heading, f"章节 {section_index}")
                    )
                    section_index += 1
                    buffer = []
                heading = text
            else:
                buffer.append(text)
        if buffer or heading:
            sections.append(ExtractedSection("\n".join(buffer), heading, f"章节 {section_index}"))
        for table_index, table in enumerate(document.tables, 1):
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            sections.append(
                ExtractedSection("\n".join(rows), f"表格 {table_index}", f"表格 {table_index}")
            )
        return sections, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".pptx":
        presentation = Presentation(io.BytesIO(data))
        sections = []
        for slide_index, slide in enumerate(presentation.slides, 1):
            parts: list[str] = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and str(shape.text).strip():
                    parts.append(str(shape.text).strip())
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            title = ""
            if slide.shapes.title is not None:
                title = slide.shapes.title.text.strip()
            sections.append(
                ExtractedSection("\n".join(parts), title, f"第 {slide_index} 页幻灯片", {"slide": slide_index})
            )
        return sections, "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if suffix in {".txt", ".md", ".csv", ".json", ".html", ".htm"}:
        decoded = data.decode("utf-8-sig", errors="replace")
        if suffix in {".html", ".htm"}:
            decoded, title = html_to_text(decoded)
            return [ExtractedSection(decoded, title)], "text/html"
        if suffix == ".csv":
            rows = list(csv.reader(io.StringIO(decoded)))
            decoded = "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
        return [ExtractedSection(decoded)], "text/plain"
    if suffix == ".ppt":
        raise ValueError("旧版 .ppt 需先另存为 .pptx 后上传")
    if suffix == ".doc":
        raise ValueError("旧版 .doc 需先另存为 .docx 后上传")
    raise ValueError("支持 PDF、DOCX、PPTX、TXT、MD、CSV、JSON 和 HTML 文件")


def _sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[。！？!?；;\.])\s+|\n+", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def _pack_units(units: list[str], target: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    length = 0
    for unit in units:
        if len(unit) > target:
            if buffer:
                chunks.append("\n".join(buffer))
                buffer, length = [], 0
            step = max(1, target - overlap)
            chunks.extend(unit[start : start + target] for start in range(0, len(unit), step))
            continue
        if buffer and length + len(unit) + 1 > target:
            packed = "\n".join(buffer)
            chunks.append(packed)
            tail = packed[-overlap:] if overlap else ""
            buffer = [tail, unit] if tail else [unit]
            length = sum(map(len, buffer)) + len(buffer) - 1
        else:
            buffer.append(unit)
            length += len(unit) + 1
    if buffer:
        chunks.append("\n".join(buffer))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def hierarchical_chunks(
    sections: list[ExtractedSection],
    *,
    parent_size: int = 1600,
    child_size: int = 480,
    child_overlap: int = 80,
) -> tuple[list[ChunkDraft], dict[str, int]]:
    """Structure-aware parent/child segmentation with sentence-boundary overlap."""

    drafts: list[ChunkDraft] = []
    fingerprints: set[str] = set()
    duplicate_chunks = 0
    parent_index = 0
    for section in sections:
        cleaned, _stats = clean_text(section.text)
        if not cleaned:
            continue
        prefix = f"# {section.heading}\n\n" if section.heading else ""
        parents = _pack_units(_sentences(cleaned), parent_size, 0)
        for parent in parents:
            parent_content = f"{prefix}{parent}".strip()
            parent_meta = {**section.metadata, "heading": section.heading, "locator": section.locator}
            drafts.append(ChunkDraft(parent_content, "parent", None, parent_meta))
            children = _pack_units(_sentences(parent), child_size, child_overlap)
            for child in children:
                child_content = f"{prefix}{child}".strip()
                fingerprint = hashlib.sha256(
                    re.sub(r"[\W_]", "", child_content).lower().encode("utf-8")
                ).hexdigest()
                if fingerprint in fingerprints:
                    duplicate_chunks += 1
                    continue
                fingerprints.add(fingerprint)
                drafts.append(ChunkDraft(child_content, "child", parent_index, parent_meta))
            parent_index += 1
    return drafts, {"duplicate_chunks_removed": duplicate_chunks}


def estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    other = max(0, len(text) - chinese)
    return max(1, chinese + other // 4)
