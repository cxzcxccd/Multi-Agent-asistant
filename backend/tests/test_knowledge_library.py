"""知识库正文浏览、条件刷新与角色权限测试。"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token
from app.modules.knowledge.embeddings import LocalHashEmbeddingProvider
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.router import get_knowledge_service
from app.modules.knowledge.schemas import KnowledgeChunk
from app.modules.knowledge.service import KnowledgeService


class OfflineVectorStore:
    def ensure_collection(self, *args):
        raise AssertionError("浏览知识正文不能请求向量数据库")


@pytest.fixture
def library_repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'library.db'}")
    Base.metadata.create_all(engine)
    repository = KnowledgeRepository(sessionmaker(bind=engine, expire_on_commit=False))
    repository.replace_all([
        KnowledgeChunk(document_key="returns", title="退换货文档", category="return_policy",
                       section="第二节", content="实际正文二", source="returns.md#第二节", position=1),
        KnowledgeChunk(document_key="returns", title="退换货文档", category="return_policy",
                       section="第一节", content="实际正文一", source="returns.md#第一节", position=0),
        KnowledgeChunk(document_key="payments", title="退款文档", category="payment_policy",
                       section="到账", content="退款说明", source="payments.md#到账", position=0),
    ])
    service = KnowledgeService(repository, LocalHashEmbeddingProvider(), OfflineVectorStore())
    app.dependency_overrides[get_knowledge_service] = lambda: service
    yield repository
    app.dependency_overrides.pop(get_knowledge_service, None)
    engine.dispose()


def request(headers=None):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/staff/knowledge/documents", headers=headers)
    return asyncio.run(run())


def auth(role):
    return {"Authorization": "Bearer " + create_access_token(Principal(
        subject=role, display_name=role, role=role, buyer_id="A" if role == "buyer" else None,
    ))}


def test_browse_current_content_without_vector_database(library_repository):
    response = request(auth("staff"))
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] == 2
    assert data["total_chunks"] == 3
    document = data["documents"][1]
    assert document["title"] == "退换货文档"
    assert [chunk["position"] for chunk in document["chunks"]] == [0, 1]
    assert document["chunks"][0]["content"] == "实际正文一"
    assert document["updated_at"].endswith("Z")
    assert request(auth("admin")).status_code == 200


def test_conditional_refresh_changes_after_database_update(library_repository):
    headers = auth("staff")
    original = request(headers)
    headers["If-None-Match"] = original.headers["etag"]
    assert request(headers).status_code == 304
    chunks = library_repository.list_chunks()
    chunks[0].content = "更新后的实际正文"
    library_repository.replace_all(chunks)
    changed = request(headers)
    assert changed.status_code == 200
    assert changed.headers["etag"] != original.headers["etag"]
    assert changed.json()["documents"][0]["chunks"][0]["content"] == "更新后的实际正文"
    library_repository.replace_all([])
    empty = request(headers)
    assert empty.json()["total_documents"] == 0
    assert empty.json()["total_chunks"] == 0


def test_library_requires_staff_or_admin(library_repository):
    assert request().status_code == 401
    assert request(auth("buyer")).status_code == 403
