from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from array import array
from dataclasses import dataclass
import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import KnowledgeEmbedding, KnowledgeProviderConfig
from .secrets import secret_store


DEFAULT_EMBEDDING_URL = "https://api.siliconflow.cn/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-VL-Embedding-8B"
DEFAULT_RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


@dataclass
class VectorResult:
    chunk_id: str
    score: float


async def get_knowledge_config(db: AsyncSession) -> KnowledgeProviderConfig:
    config = await db.get(KnowledgeProviderConfig, "default")
    if config is None:
        config = KnowledgeProviderConfig(id="default")
        db.add(config)
        await db.flush()
    return config


def knowledge_api_key(config: KnowledgeProviderConfig) -> str:
    encrypted = config.api_key_ciphertext
    if encrypted:
        return secret_store.decrypt(encrypted)
    return os.environ.get("EVO_SILICONFLOW_API_KEY", "")


def _normalize(vector: list[float]) -> tuple[list[float], float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector], norm


def pack_vector(vector: list[float]) -> tuple[bytes, float]:
    normalized, original_norm = _normalize(vector)
    values = array("f", normalized)
    return values.tobytes(), original_norm


def unpack_vector(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def local_hash_embedding(text: str, dimensions: int = 384) -> list[float]:
    """Deterministic offline fallback; production config defaults to SiliconFlow."""

    vector = [0.0] * dimensions
    normalized = re.sub(r"\s+", "", text.lower())
    tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", normalized)
    tokens += [normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    return _normalize(vector)[0]


class EmbeddingClient:
    def __init__(self, config: KnowledgeProviderConfig) -> None:
        self.url = config.embedding_base_url or DEFAULT_EMBEDDING_URL
        self.model = config.embedding_model or DEFAULT_EMBEDDING_MODEL
        self.api_key = knowledge_api_key(config)
        self.batch_size = max(1, min(config.embedding_batch_size or 16, 64))

    @property
    def provider_name(self) -> str:
        return "siliconflow" if self.api_key else "local-hash-fallback"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            return [local_hash_embedding(text) for text in texts]
        output: list[list[float]] = []
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=90) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                last_error = ""
                for attempt in range(3):
                    try:
                        response = await client.post(
                            self.url,
                            headers=headers,
                            json={"input": batch, "model": self.model},
                        )
                        response.raise_for_status()
                        payload = response.json()
                        items = sorted(payload.get("data") or [], key=lambda item: item["index"])
                        if len(items) != len(batch):
                            raise RuntimeError("embedding 返回数量与输入不一致")
                        output.extend([list(map(float, item["embedding"])) for item in items])
                        last_error = ""
                        break
                    except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                        last_error = str(exc)[:300]
                        if attempt < 2:
                            await asyncio.sleep(0.4 * (2**attempt))
                if last_error:
                    raise RuntimeError(f"SiliconFlow embedding 调用失败：{last_error}")
        return output


class RerankClient:
    def __init__(
        self, config: KnowledgeProviderConfig, *, model_override: str = ""
    ) -> None:
        self.url = config.rerank_base_url or DEFAULT_RERANK_URL
        self.model = model_override or config.rerank_model or DEFAULT_RERANK_MODEL
        self.api_key = knowledge_api_key(config)

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        if not self.api_key:
            query_terms = self._features(query)
            scored = []
            for index, document in enumerate(documents):
                terms = self._features(document)
                scored.append((index, len(query_terms & terms) / max(1, len(query_terms))))
            return sorted(scored, key=lambda item: item[1], reverse=True)[:top_n]
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
            "return_documents": False,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            if response.is_error:
                raise RuntimeError(
                    f"SiliconFlow rerank 调用失败（HTTP {response.status_code}）：{response.text[:300]}"
                )
            results = response.json().get("results") or []
        return [
            (int(item["index"]), float(item.get("relevance_score", item.get("score", 0))))
            for item in results
        ]

    @staticmethod
    def _features(value: str) -> set[str]:
        lowered = value.lower()
        features = set(re.findall(r"[a-z0-9_\-]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
        features.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
        return features


class SQLiteVectorStore:
    """Portable float32 vector storage optimized for a local Windows SQLite deployment."""

    async def upsert_many(
        self,
        db: AsyncSession,
        *,
        knowledge_base_id: str,
        chunk_ids: list[str],
        contents: list[str],
        vectors: list[list[float]],
        provider: str,
        model: str,
    ) -> None:
        if not (len(chunk_ids) == len(contents) == len(vectors)):
            raise ValueError("向量写入参数长度不一致")
        if chunk_ids:
            await db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id.in_(chunk_ids)))
        for chunk_id, content, vector in zip(chunk_ids, contents, vectors, strict=True):
            blob, norm = pack_vector(vector)
            db.add(
                KnowledgeEmbedding(
                    chunk_id=chunk_id,
                    knowledge_base_id=knowledge_base_id,
                    provider=provider,
                    model=model,
                    dimensions=len(vector),
                    vector=blob,
                    norm=norm,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
            )

    async def search(
        self,
        db: AsyncSession,
        query_vector: list[float],
        knowledge_base_ids: list[str],
        limit: int,
    ) -> list[VectorResult]:
        query, _ = _normalize(query_vector)
        statement = select(KnowledgeEmbedding)
        if knowledge_base_ids:
            statement = statement.where(
                KnowledgeEmbedding.knowledge_base_id.in_(knowledge_base_ids)
            )
        rows = (await db.scalars(statement)).all()
        scored: list[VectorResult] = []
        for row in rows:
            vector = unpack_vector(row.vector)
            if len(vector) != len(query):
                continue
            score = sum(left * right for left, right in zip(query, vector, strict=True))
            scored.append(VectorResult(row.chunk_id, score))
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


vector_store = SQLiteVectorStore()
