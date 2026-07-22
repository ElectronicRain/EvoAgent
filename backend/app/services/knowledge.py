from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    KnowledgeBase,
    KnowledgeBaseGroupMember,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeProviderConfig,
    ModelEndpoint,
)
from .common import audit, dumps, loads
from .knowledge_processing import (
    ChunkDraft,
    ExtractedSection,
    clean_text,
    estimate_tokens,
    extract_sections,
    hierarchical_chunks,
)
from .knowledge_vector import EmbeddingClient, RerankClient, get_knowledge_config, vector_store
from .llm import get_provider, provider_from_endpoint


def chunk_text(content: str, chunk_size: int = 480, overlap: int = 80) -> list[str]:
    """Compatibility wrapper around the structure-aware child chunker."""

    drafts, _ = hierarchical_chunks(
        [ExtractedSection(content)], parent_size=max(1200, chunk_size * 3), child_size=chunk_size, child_overlap=overlap
    )
    return [draft.content for draft in drafts if draft.level == "child"]


def extract_document(filename: str, data: bytes) -> tuple[str, str]:
    sections, mime_type = extract_sections(filename, data)
    return "\n\n".join(section.text for section in sections), mime_type


def _fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u3400-\u9fff]{2,}", query)
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])


def _lexical_features(value: str) -> set[str]:
    lowered = value.lower()
    words = set(re.findall(r"[a-z0-9_\-]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


class KnowledgeService:
    async def add_document(
        self,
        db: AsyncSession,
        knowledge_base_id: str,
        *,
        title: str,
        content: str,
        source: str,
        mime_type: str = "text/plain",
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeDocument:
        document, _ = await self.add_sections(
            db,
            knowledge_base_id,
            title=title,
            sections=[ExtractedSection(content)],
            source=source,
            mime_type=mime_type,
            source_id=source_id,
            metadata=metadata,
        )
        if document is None:
            raise RuntimeError("文档没有可用内容")
        return document

    async def add_sections(
        self,
        db: AsyncSession,
        knowledge_base_id: str,
        *,
        title: str,
        sections: list[ExtractedSection],
        source: str,
        mime_type: str = "text/plain",
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeDocument | None, dict[str, Any]]:
        knowledge_base = await db.get(KnowledgeBase, knowledge_base_id)
        if not knowledge_base:
            raise LookupError("知识库不存在")
        cleaned_sections: list[ExtractedSection] = []
        aggregate_stats: defaultdict[str, int] = defaultdict(int)
        for section in sections:
            cleaned, stats = clean_text(section.text)
            for key, value in stats.items():
                aggregate_stats[key] += value
            if cleaned:
                cleaned_sections.append(
                    ExtractedSection(cleaned, section.heading, section.locator, section.metadata)
                )
        combined = "\n\n".join(section.text for section in cleaned_sections)
        if not combined:
            return None, {"chunks": 0, "duplicate": False, "reason": "empty_after_cleaning"}
        content_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        duplicate = await db.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.content_hash == content_hash,
            )
        )
        if duplicate:
            return duplicate, {"chunks": 0, "duplicate": True}

        drafts, chunk_stats = hierarchical_chunks(cleaned_sections)
        aggregate_stats.update(chunk_stats)
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base_id,
            source_id=source_id,
            title=title,
            source=source,
            mime_type=mime_type,
            content_hash=content_hash,
            char_count=len(combined),
            metadata_json=dumps(metadata or {}),
            cleaning_stats_json=dumps(dict(aggregate_stats)),
            status="processing",
        )
        db.add(document)
        await db.flush()

        parent_ids: dict[int, str] = {}
        parent_number = 0
        child_chunks: list[KnowledgeChunk] = []
        for index, draft in enumerate(drafts):
            parent_id = None
            if draft.level == "child" and draft.parent_index is not None:
                parent_id = parent_ids[draft.parent_index]
            chunk = self._chunk_model(
                document,
                knowledge_base_id,
                title,
                source,
                index,
                draft,
                parent_id,
            )
            db.add(chunk)
            await db.flush()
            if draft.level == "parent":
                parent_ids[parent_number] = chunk.id
                parent_number += 1
            else:
                child_chunks.append(chunk)
                await db.execute(
                    text(
                        "INSERT INTO knowledge_chunks_fts(chunk_id, title, content) "
                        "VALUES (:id, :title, :content)"
                    ),
                    {"id": chunk.id, "title": title, "content": chunk.content},
                )

        if child_chunks:
            config = await get_knowledge_config(db)
            embedder = EmbeddingClient(config)
            vectors = await embedder.embed([chunk.content for chunk in child_chunks])
            await vector_store.upsert_many(
                db,
                knowledge_base_id=knowledge_base_id,
                chunk_ids=[chunk.id for chunk in child_chunks],
                contents=[chunk.content for chunk in child_chunks],
                vectors=vectors,
                provider=embedder.provider_name,
                model=embedder.model,
            )
        document.status = "ready"
        knowledge_base.document_count += 1
        await audit(
            db,
            "knowledge.document_added",
            "knowledge_document",
            document.id,
            {
                "title": title,
                "parent_chunks": len(parent_ids),
                "child_chunks": len(child_chunks),
                "embedding_model": (await get_knowledge_config(db)).embedding_model,
            },
        )
        return document, {
            "chunks": len(child_chunks),
            "parents": len(parent_ids),
            "duplicate": False,
            "cleaning": dict(aggregate_stats),
        }

    @staticmethod
    def _chunk_model(
        document: KnowledgeDocument,
        knowledge_base_id: str,
        title: str,
        source: str,
        index: int,
        draft: ChunkDraft,
        parent_id: str | None,
    ) -> KnowledgeChunk:
        locator = str(draft.metadata.get("locator") or f"片段 {index + 1}")
        return KnowledgeChunk(
            document_id=document.id,
            knowledge_base_id=knowledge_base_id,
            chunk_index=index,
            title=title,
            content=draft.content,
            citation=f"{title}，{locator}，来源：{source}",
            parent_chunk_id=parent_id,
            level=draft.level,
            token_count=estimate_tokens(draft.content),
            content_hash=hashlib.sha256(draft.content.encode("utf-8")).hexdigest(),
            metadata_json=dumps(draft.metadata),
        )

    async def search(
        self,
        db: AsyncSession,
        query: str,
        knowledge_base_ids: list[str] | None = None,
        top_k: int = 5,
        knowledge_group_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        result = await self.query(
            db,
            query=query,
            knowledge_base_ids=knowledge_base_ids or [],
            knowledge_group_ids=knowledge_group_ids or [],
            top_k=top_k,
            generate_answer=False,
        )
        return result["chunks"]

    async def query(
        self,
        db: AsyncSession,
        *,
        query: str,
        knowledge_base_ids: list[str],
        knowledge_group_ids: list[str] | None = None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        generate_answer: bool = True,
    ) -> dict[str, Any]:
        requested_scope = bool(knowledge_base_ids or knowledge_group_ids)
        resolved_ids = set(knowledge_base_ids)
        if knowledge_group_ids:
            grouped_ids = (
                await db.scalars(
                    select(KnowledgeBaseGroupMember.knowledge_base_id).where(
                        KnowledgeBaseGroupMember.group_id.in_(knowledge_group_ids)
                    )
                )
            ).all()
            resolved_ids.update(grouped_ids)
        scoped_base_ids = sorted(resolved_ids)
        if requested_scope and not scoped_base_ids:
            return {
                "answer": "所选知识库分组中还没有知识库或可检索资料。",
                "query": query,
                "rewritten_queries": [query],
                "chunks": [],
                "citations": [],
                "trace": {
                    "scope": "empty",
                    "knowledge_base_ids": [],
                    "knowledge_group_ids": knowledge_group_ids or [],
                    "dense_candidates": 0,
                    "lexical_candidates": 0,
                    "reranked": 0,
                },
            }
        config = await get_knowledge_config(db)
        final_k = max(1, min(top_k or config.top_k, 20))
        pool_k = max(final_k, min(candidate_k or config.candidate_k, 100))
        rewrites = await self._rewrite_query(db, config, query)
        embedder = EmbeddingClient(config)
        query_vectors = await embedder.embed(rewrites)

        dense_rankings: list[list[str]] = []
        dense_scores: dict[str, float] = {}
        for vector in query_vectors:
            results = await vector_store.search(db, vector, scoped_base_ids, pool_k)
            dense_rankings.append([item.chunk_id for item in results])
            for item in results:
                dense_scores[item.chunk_id] = max(dense_scores.get(item.chunk_id, -1), item.score)
        lexical_ids, lexical_scores = await self._lexical_search(
            db, rewrites, scoped_base_ids, pool_k
        )

        fused: defaultdict[str, float] = defaultdict(float)
        for ranking in [*dense_rankings, lexical_ids]:
            for rank, chunk_id in enumerate(ranking, 1):
                fused[chunk_id] += 1 / (60 + rank)
        candidate_ids = [
            chunk_id for chunk_id, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)[:pool_k]
        ]
        if not candidate_ids:
            return {
                "answer": "知识库中没有检索到与问题相关的内容。",
                "query": query,
                "rewritten_queries": rewrites,
                "chunks": [],
                "citations": [],
                "trace": {
                    "scope": "selected" if requested_scope else "all",
                    "knowledge_base_ids": scoped_base_ids,
                    "knowledge_group_ids": knowledge_group_ids or [],
                    "dense_candidates": 0,
                    "lexical_candidates": 0,
                    "reranked": 0,
                },
            }
        models = (await db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(candidate_ids)))).all()
        by_id = {item.id: item for item in models}
        ordered = [by_id[item] for item in candidate_ids if item in by_id]
        rerank_error = ""
        try:
            reranked = await RerankClient(config).rerank(
                query, [item.content for item in ordered], min(pool_k, len(ordered))
            )
        except RuntimeError as exc:
            rerank_error = str(exc)
            reranked = [(index, fused[item.id]) for index, item in enumerate(ordered)]
        selected = self._select_diverse(ordered, reranked, final_k)
        parent_ids = {item.parent_chunk_id for item, _score in selected if item.parent_chunk_id}
        parents = (
            await db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(parent_ids)))
        ).all() if parent_ids else []
        parent_by_id = {item.id: item for item in parents}

        chunks: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        for number, (item, rerank_score) in enumerate(selected, 1):
            parent = parent_by_id.get(item.parent_chunk_id or "")
            context_content = parent.content if parent else item.content
            metadata = loads(item.metadata_json, {})
            chunk_row = {
                "id": item.id,
                "title": item.title,
                "content": item.content,
                "context": context_content,
                "citation": item.citation,
                "knowledge_base_id": item.knowledge_base_id,
                "document_id": item.document_id,
                "score": rerank_score,
                "dense_score": dense_scores.get(item.id),
                "lexical_score": lexical_scores.get(item.id),
                "metadata": metadata,
            }
            chunks.append(chunk_row)
            citations.append(
                {
                    "number": number,
                    "chunk_id": item.id,
                    "document_id": item.document_id,
                    "title": item.title,
                    "source": item.citation,
                    "locator": metadata.get("locator", ""),
                    "score": rerank_score,
                }
            )
        context = self._assemble_context(chunks, config.context_char_budget)
        answer = await self._generate_answer(db, config, query, context) if generate_answer else ""
        return {
            "answer": answer,
            "query": query,
            "rewritten_queries": rewrites,
            "chunks": chunks,
            "citations": citations,
            "trace": {
                "embedding_provider": embedder.provider_name,
                "embedding_model": embedder.model,
                "dense_candidates": len(set().union(*map(set, dense_rankings))) if dense_rankings else 0,
                "lexical_candidates": len(lexical_ids),
                "fused_candidates": len(candidate_ids),
                "reranked": len(reranked),
                "rerank_model": config.rerank_model,
                "rerank_error": rerank_error,
                "context_chars": len(context),
                "scope": "selected" if requested_scope else "all",
                "knowledge_base_ids": scoped_base_ids,
                "knowledge_group_ids": knowledge_group_ids or [],
            },
        }

    async def _rewrite_query(
        self, db: AsyncSession, config: KnowledgeProviderConfig, query: str
    ) -> list[str]:
        normalized = re.sub(r"\s+", " ", query).strip()
        if config.llm_endpoint_id:
            endpoint = await db.get(ModelEndpoint, config.llm_endpoint_id)
            if endpoint and endpoint.enabled:
                try:
                    response = await provider_from_endpoint(endpoint).chat(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "你是知识库检索查询改写器。保留专有名词和原意，输出 1-3 条互补检索语句，"
                                    "每行一条，不要编号、解释或虚构信息。"
                                ),
                            },
                            {"role": "user", "content": normalized},
                        ],
                        model=endpoint.default_model,
                        temperature=0.1,
                    )
                    values = [re.sub(r"^[-\d.、\s]+", "", line).strip() for line in response.content.splitlines()]
                    values = [value for value in values if value][:3]
                    if values:
                        return list(dict.fromkeys([normalized, *values]))[:3]
                except Exception:
                    pass
        keywords = " ".join(re.findall(r"[A-Za-z0-9_\-]+|[\u3400-\u9fff]{2,}", normalized))
        return list(dict.fromkeys([normalized, keywords]))[:2]

    async def _lexical_search(
        self,
        db: AsyncSession,
        queries: list[str],
        knowledge_base_ids: list[str],
        limit: int,
    ) -> tuple[list[str], dict[str, float]]:
        best: dict[str, float] = {}
        for query in queries:
            expression = _fts_query(query)
            if not expression:
                continue
            sql = (
                "SELECT c.id, bm25(knowledge_chunks_fts) AS rank "
                "FROM knowledge_chunks_fts f JOIN knowledge_chunks c ON c.id=f.chunk_id "
                "WHERE knowledge_chunks_fts MATCH :query AND c.level='child'"
            )
            parameters: dict[str, Any] = {"query": expression, "limit": limit}
            if knowledge_base_ids:
                placeholders = ",".join(f":kb{i}" for i in range(len(knowledge_base_ids)))
                sql += f" AND c.knowledge_base_id IN ({placeholders})"
                parameters.update({f"kb{i}": value for i, value in enumerate(knowledge_base_ids)})
            sql += " ORDER BY rank LIMIT :limit"
            try:
                rows = (await db.execute(text(sql), parameters)).mappings().all()
            except Exception:
                rows = []
            for row in rows:
                score = -float(row["rank"])
                best[str(row["id"])] = max(best.get(str(row["id"]), float("-inf")), score)
        # unicode61 does not consistently segment Chinese technical terms on every bundled
        # SQLite build, so supplement BM25 with deterministic word/CJK-bigram coverage.
        feature_sets = [_lexical_features(query) for query in queries]
        statement = select(KnowledgeChunk).where(KnowledgeChunk.level == "child")
        if knowledge_base_ids:
            statement = statement.where(KnowledgeChunk.knowledge_base_id.in_(knowledge_base_ids))
        chunks = (await db.scalars(statement.limit(10_000))).all()
        for chunk in chunks:
            document_features = _lexical_features(chunk.content)
            coverage = max(
                (
                    len(query_features & document_features) / max(1, len(query_features))
                    for query_features in feature_sets
                ),
                default=0,
            )
            if coverage > 0:
                best[chunk.id] = max(best.get(chunk.id, 0), coverage)
        ordered = sorted(best, key=lambda chunk_id: best[chunk_id], reverse=True)[:limit]
        return ordered, best

    @staticmethod
    def _select_diverse(
        chunks: list[KnowledgeChunk], reranked: list[tuple[int, float]], top_k: int
    ) -> list[tuple[KnowledgeChunk, float]]:
        selected: list[tuple[KnowledgeChunk, float]] = []
        per_document: defaultdict[str, int] = defaultdict(int)
        seen_hashes: set[str] = set()
        for index, score in reranked:
            if index < 0 or index >= len(chunks):
                continue
            chunk = chunks[index]
            if chunk.content_hash in seen_hashes or per_document[chunk.document_id] >= 3:
                continue
            selected.append((chunk, score))
            seen_hashes.add(chunk.content_hash)
            per_document[chunk.document_id] += 1
            if len(selected) >= top_k:
                break
        return selected

    @staticmethod
    def _assemble_context(chunks: list[dict[str, Any]], char_budget: int) -> str:
        blocks: list[str] = []
        used = 0
        seen: set[str] = set()
        for index, item in enumerate(chunks, 1):
            content = str(item["context"])
            fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            block = f"[资料 {index}] {item['citation']}\n{content}"
            remaining = char_budget - used
            if remaining <= 0:
                break
            blocks.append(block[:remaining])
            used += min(len(block), remaining)
        return "\n\n".join(blocks)

    async def _generate_answer(
        self, db: AsyncSession, config: KnowledgeProviderConfig, query: str, context: str
    ) -> str:
        if not context:
            return "知识库中没有检索到足以回答该问题的资料。"
        system = (
            "你是 EvoAgent 的知识库问答助手。只能依据给定资料作答；无法从资料确认时要明确说明。"
            "关键陈述后使用 [资料 N] 标注来源，不得编造引用。先直接回答，再给必要说明。\n\n"
            f"检索资料：\n{context}"
        )
        endpoint = await db.get(ModelEndpoint, config.llm_endpoint_id) if config.llm_endpoint_id else None
        provider = provider_from_endpoint(endpoint) if endpoint and endpoint.enabled else get_provider("demo")
        model = endpoint.default_model if endpoint and endpoint.enabled else "demo-model"
        response = await provider.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": query}],
            model=model,
            temperature=0.2,
        )
        if endpoint and endpoint.enabled:
            return response.content
        # The old demo provider is intentionally bypassed here so offline RAG remains readable.
        first = context.split("\n\n", 1)[0]
        excerpt = first.split("\n", 1)[-1][:1000]
        return f"根据当前知识库资料，与问题最相关的内容如下：\n\n{excerpt}\n\n[资料 1]\n\n请配置生成模型端点以获得综合推理答案。"

    async def reindex(self, db: AsyncSession, knowledge_base_id: str) -> dict[str, Any]:
        config = await get_knowledge_config(db)
        chunks = (
            await db.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.knowledge_base_id == knowledge_base_id,
                    KnowledgeChunk.level == "child",
                )
            )
        ).all()
        embedder = EmbeddingClient(config)
        vectors = await embedder.embed([item.content for item in chunks])
        await vector_store.upsert_many(
            db,
            knowledge_base_id=knowledge_base_id,
            chunk_ids=[item.id for item in chunks],
            contents=[item.content for item in chunks],
            vectors=vectors,
            provider=embedder.provider_name,
            model=embedder.model,
        )
        return {"knowledge_base_id": knowledge_base_id, "embedded_chunks": len(chunks), "model": embedder.model}

    async def stats(self, db: AsyncSession) -> dict[str, int]:
        return {
            "bases": int(await db.scalar(select(func.count(KnowledgeBase.id))) or 0),
            "documents": int(await db.scalar(select(func.count(KnowledgeDocument.id))) or 0),
            "chunks": int(await db.scalar(select(func.count(KnowledgeChunk.id))) or 0),
        }


knowledge_service = KnowledgeService()
