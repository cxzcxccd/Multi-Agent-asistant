"""BM25 与 BGE 双路召回，通过 RRF 融合排名。"""

from dataclasses import dataclass, replace
from time import perf_counter

from app.core.tracing import traced, span, emit

from app.core.config import settings
from app.modules.knowledge.bm25 import BM25Index
from app.modules.knowledge.embeddings import EmbeddingProvider, create_embedding_provider
from app.modules.knowledge.fusion import reciprocal_rank_fusion
from app.modules.knowledge.hyde import HyDEGenerator, ModelHyDEGenerator
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.reranker import KnowledgeReranker
from app.modules.knowledge.schemas import (
    KnowledgeCategory, KnowledgeChunk, KnowledgeIndexStatus,
    KnowledgeSearchItem, KnowledgeSearchResponse, RetrievalStrategy,
)
from app.modules.knowledge.vector_store import (
    KnowledgeVectorStore, VectorStoreUnavailableError,
    build_vector_id, create_knowledge_vector_store,
)


@dataclass(frozen=True)
class RankedChunk:
    """保留原始分数和排名，方便核对融合及重排过程。"""

    chunk: KnowledgeChunk
    score: float
    keyword_score: float
    vector_score: float
    keyword_rank: int | None
    vector_rank: int | None
    rrf_score: float | None = None


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: KnowledgeVectorStore | None = None,
        minimum_score: float | None = None,
        reranker: KnowledgeReranker | None = None,
        bm25_minimum_score: float | None = None,
        rrf_rank_constant: int | None = None,
        hyde_generator: HyDEGenerator | None = None,
        default_strategy: RetrievalStrategy | None = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider or create_embedding_provider()
        self.vector_store = vector_store or create_knowledge_vector_store()
        # 兼容旧参数名称，该值现在只筛选向量相似度。
        self.minimum_score = settings.knowledge_min_score if minimum_score is None else minimum_score
        self.bm25_minimum_score = (
            settings.knowledge_bm25_min_score if bm25_minimum_score is None else bm25_minimum_score
        )
        self.rrf_rank_constant = (
            settings.knowledge_rrf_rank_constant if rrf_rank_constant is None else rrf_rank_constant
        )
        self.reranker = reranker
        self.hyde_generator = hyde_generator
        self.default_strategy = (
            settings.knowledge_retrieval_strategy
            if default_strategy is None
            else default_strategy
        )

    @traced("retrieval")
    def search(
        self,
        query: str,
        category: KnowledgeCategory | None = None,
        limit: int = 3,
        strategy: RetrievalStrategy | None = None,
    ) -> KnowledgeSearchResponse:
        strategy = strategy or self.default_strategy
        if limit < 1:
            raise ValueError("知识检索返回数量必须大于等于 1")
        if strategy not in {"keyword", "vector", "hybrid", "hybrid_rerank", "hyde"}:
            raise ValueError("未知知识检索策略")
        normalized_query = query.strip().lower()
        chunks_by_id: dict[str, KnowledgeChunk] = {}
        documents: dict[str, str] = {}
        for chunk in self.repository.list_chunks(category):
            document_id = build_vector_id(chunk.document_key, chunk.position)
            chunks_by_id[document_id] = chunk
            documents[document_id] = self._search_text(chunk)
        candidate_limit = max(limit, settings.knowledge_vector_candidates)
        keyword_scores: dict[str, float] = {}
        vector_scores: dict[str, float] = {}
        hyde_scores: dict[str, float] = {}
        hyde_status = "skipped" if strategy == "hyde" else "not_used"
        hyde_latency_ms = 0.0
        hyde_usage = None
        hyde_error_type = None
        if normalized_query and documents:
            if strategy != "vector":
                index = BM25Index(documents, settings.knowledge_bm25_k1, settings.knowledge_bm25_b)
                with span("retrieval.bm25"):
                    keyword_scores = index.score(normalized_query)
                    emit("retrieval.bm25.result", candidates=len(keyword_scores))
            if strategy != "keyword":
                with span("retrieval.vector"):
                    query_embedding = self.embedding_provider.embed_query(normalized_query)
                    self.vector_store.ensure_collection(self.embedding_provider.dimensions)
                    hits = self.vector_store.search(query_embedding, category, candidate_limit)
                    for hit in hits:
                        # 忽略没有对应正文的旧向量；重复命中只保留最高分。
                        if hit.id in chunks_by_id:
                            previous_score = vector_scores.get(hit.id, 0.0)
                            vector_scores[hit.id] = max(previous_score, hit.score)
                    emit("retrieval.vector.result", candidates=len(vector_scores))
            if strategy == "hyde":
                started = perf_counter()
                try:
                    with span("retrieval.hyde"):
                        generator = self.hyde_generator or ModelHyDEGenerator()
                        document = generator.generate(query)
                        hyde_usage = document.usage
                        if not document.text.strip():
                            raise ValueError("Empty hypothetical document")
                        # Hypothetical text is encoded as a document, not a user query.
                        embedding = self.embedding_provider.embed_documents([
                            document.text[:settings.knowledge_hyde_max_chars]
                        ])[0]
                        hits = self.vector_store.search(embedding, category, candidate_limit)
                        for hit in hits:
                            if hit.id in chunks_by_id:
                                hyde_scores[hit.id] = max(hyde_scores.get(hit.id, 0.0), hit.score)
                        hyde_status = "used"
                except Exception as error:
                    # The original query branches already succeeded. Never log generated text
                    # or exception messages, which may contain provider credentials.
                    hyde_status = "fallback"
                    hyde_error_type = type(error).__name__
                    hyde_scores = {}
                finally:
                    hyde_latency_ms = round((perf_counter() - started) * 1000, 2)
                    emit("retrieval.hyde.result", status=hyde_status,
                         duration_ms=hyde_latency_ms, usage=hyde_usage,
                         error_type=hyde_error_type, candidates=len(hyde_scores))
        keyword_ids = self._candidate_ids(keyword_scores, self.bm25_minimum_score, candidate_limit)
        vector_ids = self._candidate_ids(vector_scores, self.minimum_score, candidate_limit)
        keyword_ranks = self._ranks(keyword_ids)
        vector_ranks = self._ranks(vector_ids)
        hyde_ids = self._candidate_ids(hyde_scores, self.minimum_score, candidate_limit)
        hyde_ranks = self._ranks(hyde_ids)
        if strategy == "keyword":
            scores = self._selected_scores(keyword_ids, keyword_scores)
            score_type = "bm25"
        elif strategy == "vector":
            scores = self._selected_scores(vector_ids, vector_scores)
            score_type = "cosine"
        else:
            with span("retrieval.fusion"):
                scores = reciprocal_rank_fusion(
                    [keyword_ids, vector_ids, hyde_ids], self.rrf_rank_constant,
                )
                emit("retrieval.fusion.result", candidates=len(scores))
            score_type = "rrf"
        ranked: list[RankedChunk] = []
        for document_id, score in scores.items():
            rrf_score = score if score_type == "rrf" else None
            ranked.append(RankedChunk(
                chunk=chunks_by_id[document_id],
                score=score,
                keyword_score=keyword_scores.get(document_id, 0.0),
                vector_score=vector_scores.get(document_id, 0.0),
                keyword_rank=keyword_ranks.get(document_id),
                vector_rank=vector_ranks.get(document_id),
                rrf_score=rrf_score,
            ))
        ranked.sort(key=self._sort_key)
        if strategy == "hybrid_rerank":
            ranked = self._rerank(normalized_query, ranked)
            score_type = "rerank"
        items: list[KnowledgeSearchItem] = []
        for rank, hit in enumerate(ranked[:limit], start=1):
            rrf_score = None
            if hit.rrf_score is not None:
                rrf_score = round(hit.rrf_score, 6)
            items.append(KnowledgeSearchItem(
                document_key=hit.chunk.document_key,
                chunk_id=build_vector_id(hit.chunk.document_key, hit.chunk.position),
                chunk_position=hit.chunk.position,
                document_version=hit.chunk.document_version,
                content_hash=hit.chunk.content_hash,
                rank=rank,
                document=hit.chunk.title, category=hit.chunk.category,
                section=hit.chunk.section, content=hit.chunk.content, source=hit.chunk.source,
                score=round(hit.score, 6), keyword_score=round(hit.keyword_score, 6),
                vector_score=round(hit.vector_score, 6), score_type=score_type,
                keyword_rank=hit.keyword_rank, vector_rank=hit.vector_rank, rrf_score=rrf_score,
                hyde_score=round(hyde_scores.get(build_vector_id(hit.chunk.document_key, hit.chunk.position), 0.0), 6),
                hyde_rank=hyde_ranks.get(build_vector_id(hit.chunk.document_key, hit.chunk.position)),
            ))
        retrieval_mode = strategy
        if strategy == "hyde" and hyde_status == "fallback":
            retrieval_mode = "hyde_fallback"
        if strategy in {"hybrid", "hybrid_rerank"} and not vector_ids:
            retrieval_mode = "keyword_fallback"
        emit("retrieval.result", retrieval_mode=retrieval_mode, total=len(items),
             sources=[{
                 "document": item.document, "document_key": item.document_key,
                 "chunk_id": item.chunk_id, "chunk_position": item.chunk_position,
                 "section": item.section, "source": item.source, "rank": item.rank,
                 "score": item.score, "score_type": item.score_type,
             } for item in items])
        return KnowledgeSearchResponse(
            query=query, items=items, total=len(items), retrieval_mode=retrieval_mode,
            hyde_status=hyde_status, hyde_latency_ms=hyde_latency_ms,
            hyde_usage=hyde_usage, hyde_error_type=hyde_error_type,
        )

    @staticmethod
    def _search_text(chunk: KnowledgeChunk) -> str:
        body_lines: list[str] = []
        for line in chunk.content.splitlines():
            # URL 和采集日期用于溯源，不参与词项与重排统计。
            if line.startswith("原文：") or line.startswith("采集时间："):
                continue
            body_lines.append(line)
        body = "\n".join(body_lines)
        return f"{chunk.title}\n{chunk.section}\n{body}"

    @staticmethod
    def _candidate_ids(scores: dict[str, float], minimum_score: float, limit: int) -> list[str]:
        candidates: list[tuple[str, float]] = []
        for document_id, score in scores.items():
            if score > 0 and score >= minimum_score:
                candidates.append((document_id, score))
        candidates.sort(key=lambda item: (-item[1], item[0]))
        document_ids: list[str] = []
        for document_id, _score in candidates[:limit]:
            document_ids.append(document_id)
        return document_ids

    @staticmethod
    def _ranks(document_ids: list[str]) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for rank, document_id in enumerate(document_ids, start=1):
            ranks[document_id] = rank
        return ranks

    @staticmethod
    def _selected_scores(document_ids: list[str], scores: dict[str, float]) -> dict[str, float]:
        selected: dict[str, float] = {}
        for document_id in document_ids:
            selected[document_id] = scores[document_id]
        return selected

    @staticmethod
    def _sort_key(hit: RankedChunk) -> tuple[float, str, int]:
        return (-hit.score, hit.chunk.document_key, hit.chunk.position)

    @traced("retrieval.rerank")
    def _rerank(self, query: str, ranked: list[RankedChunk]) -> list[RankedChunk]:
        if self.reranker is None:
            raise ValueError("hybrid_rerank 检索需要配置重排模型")
        candidates = ranked[:settings.knowledge_vector_candidates]
        if not candidates:
            return []
        documents: list[str] = []
        for candidate in candidates:
            documents.append(self._search_text(candidate.chunk))
        scores = self.reranker.score(query, documents)
        reranked: list[RankedChunk] = []
        for candidate, score in zip(candidates, scores, strict=True):
            reranked.append(replace(candidate, score=score))
        reranked.sort(key=self._sort_key)
        return reranked

    def status(self) -> KnowledgeIndexStatus:
        """汇总关系数据库正文和 Milvus 向量集合的状态。"""
        chunks = self.repository.list_chunks()
        model_names = set()
        for chunk in chunks:
            if chunk.embedding_model:
                model_names.add(chunk.embedding_model)
        try:
            self.vector_store.ensure_collection(self.embedding_provider.dimensions)
            embedded_chunks = self.vector_store.count()
            connected = True
        except VectorStoreUnavailableError:
            embedded_chunks = 0
            connected = False
        return KnowledgeIndexStatus(
            chunks=len(chunks), embedded_chunks=embedded_chunks, embedding_models=sorted(model_names),
            collection_name=self.vector_store.collection_name, connected=connected,
        )
