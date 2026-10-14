"""知识库检索调试接口。"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.ai.model_client import create_model_client
from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.auth.dependencies import StaffPrincipal, get_current_user
from app.modules.auth.schemas import Principal
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.evaluation import (
    ModelRagAnswerEngine,
    RagComparisonEvaluator,
    RagEvaluator,
)
from app.modules.knowledge.reranker import FastEmbedReranker
from app.modules.knowledge.indexer import KnowledgeIndexer
from app.modules.knowledge.loader import load_knowledge_directory
from app.modules.knowledge.schemas import (
    KnowledgeCategory,
    KnowledgeIndexResult,
    KnowledgeIndexStatus,
    KnowledgeSearchResponse,
    KnowledgeLibraryResponse,
    RagEvaluationReport,
    RagComparisonReport,
)
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.records import DEFAULT_CASES_PATH, list_records, new_record_directory, read_record
from app.modules.knowledge.vector_store import VectorStoreUnavailableError
from app.modules.knowledge.library import build_library

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
staff_router = APIRouter(prefix="/staff/knowledge", tags=["knowledge"])
BACKEND_ROOT = Path(__file__).resolve().parents[3]
CurrentPrincipal = Annotated[Principal, Depends(get_current_user)]


@lru_cache(maxsize=1)
def get_knowledge_service() -> KnowledgeService:
    initialize_database()
    return KnowledgeService(KnowledgeRepository(get_session_factory()))


KnowledgeServiceDependency = Annotated[KnowledgeService, Depends(get_knowledge_service)]


@staff_router.get("/documents", response_model=KnowledgeLibraryResponse)
async def knowledge_documents(
    request: Request,
    response: Response,
    service: KnowledgeServiceDependency,
    _user: StaffPrincipal,
) -> KnowledgeLibraryResponse | Response:
    """读取 RAG 当前使用的全部正文；内容变化时返回新的版本。"""
    chunks = await run_in_threadpool(service.repository.list_chunks)
    library = build_library(chunks)
    etag = f'"{library.revision}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return library


@staff_router.get("/evaluations")
async def evaluation_records(_user: StaffPrincipal) -> dict:
    return {"records": await run_in_threadpool(list_records)}


@staff_router.get("/evaluations/{record_id}")
async def evaluation_record(record_id: str, _user: StaffPrincipal) -> dict:
    try:
        return await run_in_threadpool(read_record, record_id)
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(404, "评测记录不存在或不属于当前数据集") from error


@router.get("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    query: Annotated[str, Query(min_length=2, max_length=200)],
    service: KnowledgeServiceDependency,
    _user: CurrentPrincipal,
    category: KnowledgeCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=5)] = 3,
    strategy: Literal["keyword", "vector", "hybrid", "hyde"] | None = None,
) -> KnowledgeSearchResponse:
    """供登录用户测试知识召回结果。"""

    try:
        return await run_in_threadpool(service.search, query, category, limit, strategy)
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
    strategy: Literal["keyword", "vector", "hybrid", "hyde"] = "hybrid",
) -> RagEvaluationReport:
    cases_path = DEFAULT_CASES_PATH
    answer_engine = None
    if include_answers:
        model_client = create_model_client(tools=[])
        answer_engine = ModelRagAnswerEngine(model_client)
    evaluator = RagEvaluator(service, cases_path, answer_engine, strategy=strategy)
    try:
        report = await run_in_threadpool(evaluator.run)
        output_path = new_record_directory()
        await run_in_threadpool(evaluator.save, report, output_path)
        return report
    except VectorStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 向量数据库暂不可用",
        ) from error


@staff_router.post("/evaluate/comparison", response_model=RagComparisonReport)
async def compare_knowledge_retrieval(
    service: KnowledgeServiceDependency,
    _user: StaffPrincipal,
    include_answers: bool = False,
    include_hyde: bool = False,
) -> RagComparisonReport:
    """使用同一批案例比较关键词、向量、混合与重排方案。"""

    answer_engine = None
    if include_answers:
        model_client = create_model_client(tools=[])
        answer_engine = ModelRagAnswerEngine(model_client)
    rerank_service = KnowledgeService(
        repository=service.repository,
        embedding_provider=service.embedding_provider,
        vector_store=service.vector_store,
        minimum_score=service.minimum_score,
        bm25_minimum_score=service.bm25_minimum_score,
        rrf_rank_constant=service.rrf_rank_constant,
        reranker=FastEmbedReranker(),
    )
    services = {
        "keyword": service,
        "vector": service,
        "hybrid": service,
        "hybrid_rerank": rerank_service,
    }
    if include_hyde:
        services["hyde"] = service
    cases_path = DEFAULT_CASES_PATH
    evaluator = RagComparisonEvaluator(services, cases_path, answer_engine)
    try:
        report = await run_in_threadpool(evaluator.run)
        output_path = new_record_directory()
        await run_in_threadpool(evaluator.save, report, output_path)
        return report
    except VectorStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 向量数据库暂不可用",
        ) from error
