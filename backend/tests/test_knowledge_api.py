"""知识搜索、索引管理和评测接口测试。"""

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token
from app.modules.knowledge.embeddings import LocalHashEmbeddingProvider
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.router import get_knowledge_service
from app.modules.knowledge.service import KnowledgeService
from app.db.base import Base
from app.modules.knowledge.vector_store import InMemoryKnowledgeVectorStore


def test_knowledge_endpoints_enforce_roles_and_return_real_metrics(tmp_path: Path) -> None:
    async def run() -> None:
        engine = create_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        repository = KnowledgeRepository(session_factory)
        provider = LocalHashEmbeddingProvider()
        vector_store = InMemoryKnowledgeVectorStore()
        knowledge_path = Path(__file__).resolve().parents[1] / "data" / "knowledge"
        KnowledgeIndexer(repository, provider, vector_store).rebuild(
            load_knowledge_directory(knowledge_path)
        )
        service = KnowledgeService(
            repository,
            provider,
            vector_store,
            minimum_score=0.12,
        )
        app.dependency_overrides[get_knowledge_service] = lambda: service

        buyer_token = create_access_token(
            Principal(subject="buyer", display_name="买家", role="buyer", buyer_id="A")
        )
        staff_token = create_access_token(
            Principal(subject="staff", display_name="客服", role="staff")
        )
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                search = await client.get(
                    "/api/knowledge/search",
                    params={"query": "进水能免费维修吗", "category": "warranty_policy"},
                    headers={"Authorization": f"Bearer {buyer_token}"},
                )
                forbidden = await client.post(
                    "/api/staff/knowledge/reindex",
                    headers={"Authorization": f"Bearer {buyer_token}"},
                )
                status = await client.get(
                    "/api/staff/knowledge/status",
                    headers={"Authorization": f"Bearer {staff_token}"},
                )
                evaluation = await client.post(
                    "/api/staff/knowledge/evaluate",
                    headers={"Authorization": f"Bearer {staff_token}"},
                )

                assert search.status_code == 200
                assert search.json()["retrieval_mode"] == "hybrid"
                sections = [item["section"] for item in search.json()["items"]]
                assert "保修范围" in sections
                assert forbidden.status_code == 403
                assert status.json()["embedded_chunks"] == 15
                assert status.json()["vector_database"] == "milvus"
                assert status.json()["connected"] is True
                assert evaluation.json()["cases"] == 12
                assert evaluation.json()["recall_at_k"] >= 0.9
        finally:
            app.dependency_overrides.pop(get_knowledge_service, None)
            engine.dispose()

    asyncio.run(run())
