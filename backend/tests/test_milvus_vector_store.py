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
        self.indexes: dict[str, dict] = {}
        self.index_drop_calls = 0
        self.index_create_calls = 0
        self.release_calls = 0
        self.last_search_params = {}

    def has_collection(self, _name: str, **_kwargs: object) -> bool:
        return self.exists

    def create_collection(self, **kwargs: object) -> None:
        self.exists = True
        schema = kwargs["schema"]
        for field in schema.fields:
            if field.name == "vector":
                self.dimensions = int(field.params["dim"])
        self.create_index(index_params=kwargs["index_params"])

    def list_indexes(self, _name, **_kwargs):
        return list(self.indexes)

    def describe_index(self, _name, index_name, **_kwargs):
        return self.indexes[index_name]

    def release_collection(self, _name, **_kwargs):
        self.release_calls += 1

    def drop_index(self, _name, index_name, **_kwargs):
        del self.indexes[index_name]
        self.index_drop_calls += 1

    def create_index(self, **kwargs):
        self.index_create_calls += 1
        for parameters in kwargs["index_params"]:
            values = parameters.to_dict()
            self.indexes[values["index_name"]] = values

    def describe_collection(self, _name: str, **_kwargs: object) -> dict[str, object]:
        return {"fields": [{"name": "vector", "params": {"dim": self.dimensions}}]}

    def load_collection(self, _name: str, **_kwargs: object) -> None:
        return None

    def drop_collection(self, _name: str, **_kwargs: object) -> None:
        self.exists = False
        self.rows.clear()
        self.indexes.clear()
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
        self.last_search_params = kwargs["search_params"]
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
    assert client.indexes["vector_hnsw"]["index_type"] == "HNSW"
    assert client.indexes["vector_hnsw"]["metric_type"] == "COSINE"
    assert client.last_search_params["params"]["ef"] == 64


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


def test_hnsw_migration_keeps_existing_vectors_and_is_idempotent() -> None:
    client = FakeMilvusClient()
    client.exists = True
    client.dimensions = 512
    client.rows = [{"id": "policy:0", "vector": [0.1] * 512}]
    client.indexes = {
        "vector": {"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
        "category_index": {"field_name": "category", "index_type": "INVERTED"},
    }
    store = create_store(client)
    store.ensure_collection(512)
    store.ensure_collection(512)
    assert client.drop_calls == 0
    assert client.index_drop_calls == 1
    assert client.index_create_calls == 1
    assert client.release_calls == 1
    assert client.rows[0]["id"] == "policy:0"
    assert "category_index" in client.indexes
    assert client.indexes["vector_hnsw"]["M"] == 16
    assert client.indexes["vector_hnsw"]["efConstruction"] == 200


def test_hnsw_search_width_covers_topk_and_parameter_change_rebuilds_only_index() -> None:
    client = FakeMilvusClient()
    store = create_store(client)
    store.ensure_collection(512)
    store.search([0.1] * 512, None, 100)
    assert client.last_search_params["params"]["ef"] == 100
    configuration = Settings(milvus_hnsw_m=32, milvus_hnsw_ef_construction=300)
    changed_store = MilvusKnowledgeVectorStore(configuration, client)
    changed_store.ensure_collection(512)
    assert client.drop_calls == 0
    assert client.index_drop_calls == 1
    assert client.indexes["vector_hnsw"]["M"] == 32
    assert client.indexes["vector_hnsw"]["efConstruction"] == 300


def test_failed_index_migration_exposes_error_and_preserves_rows() -> None:
    class BrokenIndexClient(FakeMilvusClient):
        def create_index(self, **kwargs):
            raise RuntimeError("index build failed")

    client = BrokenIndexClient()
    client.exists = True
    client.dimensions = 512
    client.rows = [{"id": "policy:0"}]
    store = create_store(client)
    with pytest.raises(VectorStoreUnavailableError, match="index build failed"):
        store.ensure_collection(512)
    assert client.drop_calls == 0
    assert client.rows == [{"id": "policy:0"}]
