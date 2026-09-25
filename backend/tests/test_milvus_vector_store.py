"""Milvus 客户端适配层测试。"""

import pytest

from app.core.config import Settings
from app.modules.knowledge.vector_store import (
    MilvusKnowledgeVectorStore,
    VectorRecord,
    VectorStoreUnavailableError,
)


class FakeMilvusClient:
    def __init__(self) -> None:
        self.exists = False
        self.dimensions = 0
        self.rows: list[dict[str, object]] = []
        self.last_filter = ""
        self.drop_calls = 0

    def has_collection(self, _name: str, **_kwargs: object) -> bool:
        return self.exists

    def create_collection(self, **kwargs: object) -> None:
        self.exists = True
        self.dimensions = int(kwargs["dimension"])

    def describe_collection(self, _name: str, **_kwargs: object) -> dict[str, object]:
        return {"fields": [{"name": "vector", "params": {"dim": self.dimensions}}]}

    def load_collection(self, _name: str, **_kwargs: object) -> None:
        return None

    def drop_collection(self, _name: str, **_kwargs: object) -> None:
        self.exists = False
        self.rows.clear()
        self.drop_calls += 1

    def query(self, **_kwargs: object) -> list[dict[str, object]]:
        return self.rows

    def upsert(self, **kwargs: object) -> None:
        self.rows = list(kwargs["data"])  # type: ignore[arg-type]

    def flush(self, _name: str, **_kwargs: object) -> None:
        return None

    def delete(self, **kwargs: object) -> None:
        removed_ids = set(kwargs["ids"])  # type: ignore[arg-type]
        self.rows = [row for row in self.rows if row["id"] not in removed_ids]

    def search(self, **kwargs: object) -> list[list[dict[str, object]]]:
        self.last_filter = str(kwargs["filter"])
        return [[{"id": "returns:0", "distance": 0.82}]]

    def get_collection_stats(self, _name: str, **_kwargs: object) -> dict[str, str]:
        return {"row_count": str(len(self.rows))}


def create_store(client: FakeMilvusClient) -> MilvusKnowledgeVectorStore:
    configuration = Settings(
        milvus_uri="http://milvus.test:19530",
        milvus_collection="knowledge_test",
    )
    return MilvusKnowledgeVectorStore(configuration, client)


def test_milvus_store_creates_collection_and_searches_with_category_filter() -> None:
    client = FakeMilvusClient()
    store = create_store(client)
    store.ensure_collection(256)
    store.upsert(
        [
            VectorRecord(
                id="returns:0",
                document_key="returns",
                position=0,
                category="return_policy",
                content_hash="hash-1",
                embedding_model="local-hash-v1",
                vector=[0.1] * 256,
            )
        ]
    )

    hits = store.search([0.1] * 256, "return_policy", 3)

    assert store.count() == 1
    assert hits[0].id == "returns:0"
    assert hits[0].score == 0.82
    assert client.last_filter == 'category == "return_policy"'


def test_milvus_store_recreates_collection_when_vector_dimensions_change() -> None:
    client = FakeMilvusClient()
    client.exists = True
    client.dimensions = 128
    store = create_store(client)

    store.ensure_collection(256)

    assert client.drop_calls == 1
    assert client.dimensions == 256


def test_milvus_connection_errors_are_exposed_as_domain_error() -> None:
    class BrokenClient(FakeMilvusClient):
        def has_collection(self, _name: str, **_kwargs: object) -> bool:
            raise RuntimeError("connection refused")

    store = create_store(BrokenClient())

    with pytest.raises(VectorStoreUnavailableError, match="Milvus 请求失败"):
        store.ensure_collection(256)
