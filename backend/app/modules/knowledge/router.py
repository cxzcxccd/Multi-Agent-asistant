"""知识库检索调试接口。"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.ai.model_client import create_model_client
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.auth.dependencies import StaffPrincipal, get_current_user
from app.modules.auth.schemas import Principal
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.evaluation import ModelRagAnswerEngine, RagEvaluator
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.schemas import (
    KnowledgeCategory,
    KnowledgeIndexResult,
    KnowledgeIndexStatus,
    KnowledgeSearchResponse,
    RagEvaluationReport,
)
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import VectorStoreUnavailableError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
staff_router = APIRouter(prefix="/staff/knowledge", tags=["knowledge"])
BACKEND_ROOT = Path(__file__).resolve().parents[3]
CurrentPrincipal = Annotated[Principal, Depends(get_current_user)]


@lru_cache(maxsize=1)
def get_knowledge_service() -> KnowledgeService:
    initialize_database()
    return KnowledgeService(KnowledgeRepository(get_session_factory()))


KnowledgeServiceDependency = Annotated[KnowledgeService, Depends(get_knowledge_service)]


@router.get("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    query: Annotated[str, Query(min_length=2, max_length=200)],
    service: KnowledgeServiceDependency,
    _user: CurrentPrincipal,
    category: KnowledgeCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=5)] = 3,
) -> KnowledgeSearchResponse:
    """供登录用户测试知识召回结果。"""

    try:
        return service.search(query, category, limit)
    except VectorStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 向量数据库暂不可用",
        ) from error


@staff_router.get("/status", response_model=KnowledgeIndexStatus)
async def knowledge_status(
    service: KnowledgeServiceDependency,
    _user: StaffPrincipal,
) -> KnowledgeIndexStatus:
    return service.status()


@staff_router.post("/reindex", response_model=KnowledgeIndexResult)
async def reindex_knowledge(
    service: KnowledgeServiceDependency,
    _user: StaffPrincipal,
) -> KnowledgeIndexResult:
    knowledge_path = BACKEND_ROOT / "data" / "knowledge"
    chunks = load_knowledge_directory(knowledge_path)
    indexer = KnowledgeIndexer(
        service.repository,
        service.embedding_provider,
        service.vector_store,
    )
    try:
        return await run_in_threadpool(indexer.rebuild, chunks)
    except VectorStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 向量数据库暂不可用",
        ) from error


@staff_router.post("/evaluate", response_model=RagEvaluationReport)
async def evaluate_knowledge(
    service: KnowledgeServiceDependency,
    _user: StaffPrincipal,
    include_answers: bool = False,
) -> RagEvaluationReport:
    cases_path = BACKEND_ROOT / "data" / "evaluation" / "rag_cases.json"
    answer_engine = None
    if include_answers:
        model_client = create_model_client(tools=[])
        answer_engine = ModelRagAnswerEngine(model_client)
    evaluator = RagEvaluator(service, cases_path, answer_engine)
    try:
        report = await run_in_threadpool(evaluator.run)
        output_path = BACKEND_ROOT / "data" / "evaluation" / "results"
        await run_in_threadpool(evaluator.save, report, output_path)
        return report
    except VectorStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 向量数据库暂不可用",
        ) from error
