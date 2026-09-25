"""Milvus 向量存储及测试用的内存实现。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

from pymilvus import MilvusClient

from app.core.config import Settings, settings
from app.modules.knowledge.schemas import KnowledgeCategory


class VectorStoreUnavailableError(RuntimeError):
    """Milvus 无法连接或执行请求。"""


@dataclass(frozen=True)
class VectorRecord:
    """写入 Milvus 的向量及必要过滤字段。"""

    id: str
    document_key: str
    position: int
    category: KnowledgeCategory
    content_hash: str
    embedding_model: str
    vector: list[float]


@dataclass(frozen=True)
class VectorMetadata:
    """用于判断知识片段是否需要重新向量化。"""

    id: str
    content_hash: str
    embedding_model: str


@dataclass(frozen=True)
class VectorSearchHit:
    """Milvus 相似度召回结果。"""

    id: str
    score: float


class KnowledgeVectorStore(Protocol):
    """知识索引只依赖该协议，便于测试和替换部署方式。"""

    @property
    def collection_name(self) -> str: ...

    def ensure_collection(self, dimensions: int) -> None: ...

    def list_metadata(self) -> dict[str, VectorMetadata]: ...

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def delete(self, ids: list[str]) -> None: ...

    def search(
        self,
        vector: list[float],
        category: KnowledgeCategory | None,
        limit: int,
    ) -> list[VectorSearchHit]: ...

    def count(self) -> int: ...


def build_vector_id(document_key: str, position: int) -> str:
    """使用稳定业务键关联 Milvus 向量和 SQLite 正文。"""

    return f"{document_key}:{position}"


class MilvusKnowledgeVectorStore:
    """使用 PyMilvus 连接 Standalone、Distributed 或 Zilliz Cloud。"""

    def __init__(
        self,
        configuration: Settings = settings,
        client: Any | None = None,
    ) -> None:
        self._uri = configuration.milvus_uri
        self._token = self._secret_value(configuration.milvus_token)
        self._database = configuration.milvus_database
        self._collection_name = configuration.milvus_collection
        self._timeout = configuration.milvus_timeout_seconds
        self._client = client

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def ensure_collection(self, dimensions: int) -> None:
        client = self._get_client()
        try:
            if client.has_collection(self.collection_name, timeout=self._timeout):
                current_dimensions = self._collection_dimensions(client)
                if current_dimensions == dimensions:
                    client.load_collection(self.collection_name, timeout=self._timeout)
                    return
                client.drop_collection(self.collection_name, timeout=self._timeout)

            client.create_collection(
                collection_name=self.collection_name,
                dimension=dimensions,
                primary_field_name="id",
                id_type="string",
                max_length=200,
                vector_field_name="vector",
                metric_type="COSINE",
                consistency_level="Strong",
                timeout=self._timeout,
            )
        except Exception as error:
            self._raise_unavailable(error)

    def list_metadata(self) -> dict[str, VectorMetadata]:
        client = self._get_client()
        try:
            rows = client.query(
                collection_name=self.collection_name,
                filter="",
                output_fields=["id", "content_hash", "embedding_model"],
                limit=16384,
                timeout=self._timeout,
            )
        except Exception as error:
            self._raise_unavailable(error)

        metadata: dict[str, VectorMetadata] = {}
        for row in rows:
            item = VectorMetadata(
                id=str(row["id"]),
                content_hash=str(row.get("content_hash", "")),
                embedding_model=str(row.get("embedding_model", "")),
            )
            metadata[item.id] = item
        return metadata

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        data: list[dict[str, Any]] = []
        for record in records:
            data.append(
                {
                    "id": record.id,
                    "vector": record.vector,
                    "document_key": record.document_key,
                    "position": record.position,
                    "category": record.category,
                    "content_hash": record.content_hash,
                    "embedding_model": record.embedding_model,
                }
            )
        client = self._get_client()
        try:
            client.upsert(
                collection_name=self.collection_name,
                data=data,
                timeout=self._timeout,
            )
            client.flush(self.collection_name, timeout=self._timeout)
        except Exception as error:
            self._raise_unavailable(error)

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            self._get_client().delete(
                collection_name=self.collection_name,
                ids=ids,
                timeout=self._timeout,
            )
        except Exception as error:
            self._raise_unavailable(error)

    def search(
        self,
        vector: list[float],
        category: KnowledgeCategory | None,
        limit: int,
    ) -> list[VectorSearchHit]:
        category_filter = ""
        if category is not None:
            category_filter = f'category == "{category}"'
        try:
            results = self._get_client().search(
                collection_name=self.collection_name,
                data=[vector],
                filter=category_filter,
                limit=limit,
                output_fields=["id"],
                search_params={"metric_type": "COSINE"},
                timeout=self._timeout,
            )
        except Exception as error:
            self._raise_unavailable(error)

        if not results:
            return []
        hits: list[VectorSearchHit] = []
        for result in results[0]:
            score = max(float(result.get("distance", 0.0)), 0.0)
            hits.append(VectorSearchHit(id=str(result["id"]), score=score))
        return hits

    def count(self) -> int:
        try:
            stats = self._get_client().get_collection_stats(
                self.collection_name,
                timeout=self._timeout,
            )
            return int(stats.get("row_count", 0))
        except Exception as error:
            self._raise_unavailable(error)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            self._client = MilvusClient(
                uri=self._uri,
                token=self._token,
                db_name=self._database,
                timeout=self._timeout,
            )
        except Exception as error:
            self._raise_unavailable(error)
        return self._client

    def _collection_dimensions(self, client: Any) -> int | None:
        description = client.describe_collection(
            self.collection_name,
            timeout=self._timeout,
        )
        for field in description.get("fields", []):
            if field.get("name") != "vector":
                continue
            parameters = field.get("params") or {}
            dimension = parameters.get("dim")
            return int(dimension) if dimension is not None else None
        return None

    @staticmethod
    def _secret_value(secret: Any | None) -> str:
        if secret is None:
            return ""
        return secret.get_secret_value()

    @staticmethod
    def _raise_unavailable(error: Exception) -> None:
        raise VectorStoreUnavailableError(f"Milvus 请求失败：{error}") from error


class InMemoryKnowledgeVectorStore:
    """测试使用的确定性内存实现，不连接外部 Milvus。"""

    def __init__(self, collection_name: str = "test_knowledge_chunks") -> None:
        self._collection_name = collection_name
        self._dimensions: int | None = None
        self._records: dict[str, VectorRecord] = {}

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def ensure_collection(self, dimensions: int) -> None:
        if self._dimensions is not None and self._dimensions != dimensions:
            self._records.clear()
        self._dimensions = dimensions

    def list_metadata(self) -> dict[str, VectorMetadata]:
        metadata: dict[str, VectorMetadata] = {}
        for record in self._records.values():
            metadata[record.id] = VectorMetadata(
                id=record.id,
                content_hash=record.content_hash,
                embedding_model=record.embedding_model,
            )
        return metadata

    def upsert(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._records[record.id] = record

    def delete(self, ids: list[str]) -> None:
        for vector_id in ids:
            self._records.pop(vector_id, None)

    def search(
        self,
        vector: list[float],
        category: KnowledgeCategory | None,
        limit: int,
    ) -> list[VectorSearchHit]:
        hits: list[VectorSearchHit] = []
        for record in self._records.values():
            if category is not None and record.category != category:
                continue
            score = self._cosine_similarity(vector, record.vector)
            hits.append(VectorSearchHit(id=record.id, score=score))
        hits.sort(key=lambda item: (-item.score, item.id))
        return hits[:limit]

    def count(self) -> int:
        return len(self._records)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return 0.0
        dot_product = sum(a * b for a, b in zip(left, right, strict=True))
        left_length = math.sqrt(sum(value * value for value in left))
        right_length = math.sqrt(sum(value * value for value in right))
        if left_length == 0 or right_length == 0:
            return 0.0
        return max(dot_product / (left_length * right_length), 0.0)


def create_knowledge_vector_store(
    configuration: Settings = settings,
) -> KnowledgeVectorStore:
    """创建生产环境使用的 Milvus 向量存储。"""

    return MilvusKnowledgeVectorStore(configuration)
