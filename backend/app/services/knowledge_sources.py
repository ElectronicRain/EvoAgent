from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, urlunsplit

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import KnowledgeBase, KnowledgeIngestionJob, KnowledgeSource
from .common import audit, dumps, loads
from .knowledge_processing import ExtractedSection, MainTextHTMLParser, clean_text
from .secrets import secret_store


def _public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror:
        return True  # The HTTP request will return the actionable DNS error.
    for value in addresses:
        address = ipaddress.ip_address(value)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False
    return True


def _safe_database_uri(connection_url: str) -> str:
    try:
        return make_url(connection_url).render_as_string(hide_password=True)
    except Exception:
        return "database://configured"


def _safe_http_uri(value: str) -> str:
    parsed = urlparse(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _safe_error(value: str, config: dict[str, Any]) -> str:
    message = re.sub(
        r"https?://[^\s]+",
        lambda match: _safe_http_uri(match.group(0)),
        value,
    )
    connection_url = str(config.get("connection_url") or "")
    if connection_url:
        message = message.replace(connection_url, _safe_database_uri(connection_url))
    for secret in (config.get("headers") or {}).values():
        if isinstance(secret, str) and len(secret) >= 4:
            message = message.replace(secret, "***")
    return message[:1000]


def _json_path(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class KnowledgeSourceService:
    async def create(
        self,
        db: AsyncSession,
        knowledge_base_id: str,
        *,
        name: str,
        source_type: str,
        uri: str,
        config: dict[str, Any],
    ) -> KnowledgeSource:
        if not await db.get(KnowledgeBase, knowledge_base_id):
            raise LookupError("知识库不存在")
        safe_uri = (
            _safe_database_uri(uri)
            if source_type == "database"
            else _safe_http_uri(uri) if source_type in {"web", "api"} else uri
        )
        source = KnowledgeSource(
            knowledge_base_id=knowledge_base_id,
            name=name,
            source_type=source_type,
            uri=safe_uri,
            config_ciphertext=secret_store.encrypt(dumps(config)),
            status="pending",
            metadata_json=dumps({"config_fields": sorted(config.keys())}),
        )
        db.add(source)
        await db.flush()
        await audit(db, "knowledge.source_created", "knowledge_source", source.id, {"type": source_type})
        return source

    def public_row(self, source: KnowledgeSource) -> dict[str, Any]:
        return {
            "id": source.id,
            "knowledge_base_id": source.knowledge_base_id,
            "name": source.name,
            "source_type": source.source_type,
            "uri": source.uri,
            "status": source.status,
            "last_error": source.last_error,
            "last_synced_at": source.last_synced_at,
            "metadata": loads(source.metadata_json, {}),
            "created_at": source.created_at,
            "updated_at": source.updated_at,
        }

    async def sync(self, db: AsyncSession, source_id: str) -> KnowledgeIngestionJob:
        source = await db.get(KnowledgeSource, source_id)
        if source is None:
            raise LookupError("数据源不存在")
        job = KnowledgeIngestionJob(
            knowledge_base_id=source.knowledge_base_id,
            source_id=source.id,
            status="running",
            stage="extracting",
            progress=5,
        )
        db.add(job)
        source.status = "syncing"
        source.last_error = ""
        await db.flush()
        config: dict[str, Any] = {}
        source.metadata_json = dumps({"config_fields": [], "last_job_id": job.id})
        try:
            config = loads(secret_store.decrypt(source.config_ciphertext), {})
            documents = await self._read_source(source, config)
            job.stage = "cleaning_chunking_embedding"
            job.progress = 35
            await db.flush()
            from .knowledge import knowledge_service

            chunk_count = 0
            duplicates = 0
            for document in documents:
                item, result = await knowledge_service.add_sections(
                    db,
                    source.knowledge_base_id,
                    title=document["title"],
                    sections=document["sections"],
                    source=document["source"],
                    mime_type=document.get("mime_type", "text/plain"),
                    source_id=source.id,
                    metadata=document.get("metadata", {}),
                )
                chunk_count += int(result.get("chunks", 0))
                duplicates += int(result.get("duplicate", False))
                if not item:
                    duplicates += 1
            job.document_count = len(documents) - duplicates
            job.chunk_count = chunk_count
            job.duplicate_count = duplicates
            job.stage = "completed"
            job.progress = 100
            job.status = "completed"
            source.status = "ready"
            source.last_synced_at = datetime.now(timezone.utc)
            source.metadata_json = dumps(
                {"config_fields": sorted(config.keys()), "last_job_id": job.id}
            )
            await audit(
                db,
                "knowledge.source_synced",
                "knowledge_source",
                source.id,
                {"documents": job.document_count, "chunks": chunk_count},
            )
        except Exception as exc:
            source.status = "failed"
            source.last_error = _safe_error(str(exc), config)
            job.status = "failed"
            job.stage = "failed"
            job.error = source.last_error
            job.detail_json = dumps({"error_type": type(exc).__name__})
            source.metadata_json = dumps(
                {"config_fields": sorted(config.keys()), "last_job_id": job.id}
            )
            await audit(
                db,
                "knowledge.source_sync_failed",
                "knowledge_source",
                source.id,
                {"job_id": job.id, "error_type": type(exc).__name__},
                success=False,
            )
        return job

    async def _read_source(
        self, source: KnowledgeSource, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if source.source_type == "web":
            return await self._read_web(source, config)
        if source.source_type == "api":
            return await self._read_api(source, config)
        if source.source_type == "database":
            return await self._read_database(source, config)
        raise ValueError(f"不支持同步数据源类型：{source.source_type}")

    async def _read_web(
        self, source: KnowledgeSource, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        start_url = str(config.get("url") or source.uri)
        if not _public_url(start_url):
            raise ValueError("网页地址不是可访问的公网 HTTP/HTTPS 地址")
        max_pages = max(1, min(int(config.get("max_pages", 1)), 20))
        same_domain = bool(config.get("same_domain", True))
        queue = [start_url]
        visited: set[str] = set()
        documents: list[dict[str, Any]] = []
        origin_host = urlparse(start_url).hostname
        headers = {
            "User-Agent": "EvoAgent-KnowledgeBot/1.0",
            "Accept": "text/html,text/plain,application/xhtml+xml",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            while queue and len(documents) < max_pages:
                url = queue.pop(0)
                if url in visited or not _public_url(url):
                    continue
                visited.add(url)
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                if len(response.content) > 5 * 1024 * 1024:
                    raise ValueError("单个网页内容超过 5MB 限制")
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type and "text" not in content_type:
                    continue
                parser = MainTextHTMLParser()
                parser.feed(response.text)
                text_value, _ = clean_text(parser.text)
                if text_value:
                    documents.append(
                        {
                            "title": clean_text(parser.title)[0] or url,
                            "sections": [ExtractedSection(text_value, locator=url)],
                            "source": url,
                            "mime_type": content_type.split(";")[0] or "text/html",
                            "metadata": {"url": url, "source_type": "web"},
                        }
                    )
                for link in parser.links:
                    absolute = urljoin(url, link).split("#", 1)[0]
                    if same_domain and urlparse(absolute).hostname != origin_host:
                        continue
                    if absolute not in visited and absolute not in queue:
                        queue.append(absolute)
        if not documents:
            raise ValueError("网页未提取到可用正文")
        return documents

    async def _read_api(
        self, source: KnowledgeSource, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        url = str(config.get("url") or source.uri)
        if not _public_url(url):
            raise ValueError("API 地址不是可访问的公网 HTTP/HTTPS 地址")
        method = str(config.get("method", "GET")).upper()
        if method not in {"GET", "POST"}:
            raise ValueError("第三方 API 数据源仅允许 GET 或 POST")
        headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items()}
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=config.get("params") or None,
                json=config.get("body") if method == "POST" else None,
            )
            response.raise_for_status()
            if len(response.content) > 10 * 1024 * 1024:
                raise ValueError("API 响应超过 10MB 限制")
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                data = _json_path(response.json(), str(config.get("response_path", "")))
                text_value = json.dumps(data, ensure_ascii=False, indent=2)
            elif "html" in content_type:
                parser = MainTextHTMLParser()
                parser.feed(response.text)
                text_value = parser.text
            else:
                text_value = response.text
        return [
            {
                "title": str(config.get("title") or source.name),
                "sections": [ExtractedSection(text_value, locator=url)],
                "source": url,
                "mime_type": content_type.split(";")[0] or "text/plain",
                "metadata": {"url": url, "source_type": "api"},
            }
        ]

    async def _read_database(
        self, source: KnowledgeSource, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        connection_url = str(config.get("connection_url") or "")
        query = str(config.get("query") or "").strip().rstrip(";")
        if not connection_url:
            raise ValueError("数据库连接地址不能为空")
        if not re.match(r"^(select|with)\b", query, flags=re.I):
            raise ValueError("数据库数据源只允许只读 SELECT/WITH 查询")
        row_limit = max(1, min(int(config.get("row_limit", 5000)), 20_000))

        def execute_query() -> list[dict[str, Any]]:
            engine = create_engine(connection_url)
            try:
                with engine.connect() as connection:
                    result = connection.execute(text(query), config.get("params") or {})
                    return [dict(row) for row in result.mappings().fetchmany(row_limit)]
            finally:
                engine.dispose()

        import asyncio

        rows = await asyncio.to_thread(execute_query)
        if not rows:
            raise ValueError("数据库查询没有返回记录")
        sections = [
            ExtractedSection(
                json.dumps(row, ensure_ascii=False, default=str),
                locator=f"记录 {index}",
                metadata={"row": index},
            )
            for index, row in enumerate(rows, 1)
        ]
        return [
            {
                "title": str(config.get("title") or source.name),
                "sections": sections,
                "source": source.uri,
                "mime_type": "application/x-database-query",
                "metadata": {"source_type": "database", "rows": len(rows)},
            }
        ]

    async def list_for_base(
        self, db: AsyncSession, knowledge_base_id: str
    ) -> list[KnowledgeSource]:
        return list(
            (
                await db.scalars(
                    select(KnowledgeSource)
                    .where(KnowledgeSource.knowledge_base_id == knowledge_base_id)
                    .order_by(KnowledgeSource.created_at.desc())
                )
            ).all()
        )


knowledge_source_service = KnowledgeSourceService()
