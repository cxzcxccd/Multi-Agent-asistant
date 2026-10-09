"""BM25 与 BGE 双路召回，通过 RRF 融合排名。"""

from dataclasses import dataclass, replace

from app.core.config import settings
from app.modules.knowledge.bm25 import BM25Index
from app.modules.knowledge.embeddings import EmbeddingProvider, create_embedding_provider
from app.modules.knowledge.fusion import reciprocal_rank_fusion
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

    def search(
        self,
        query: str,
        category: KnowledgeCategory | None = None,
        limit: int = 3,
        strategy: RetrievalStrategy = "hybrid",
    ) -> KnowledgeSearchResponse:
        if limit < 1:
            raise ValueError("知识检索返回数量必须大于等于 1")
        if strategy not in {"keyword", "vector", "hybrid", "hybrid_rerank"}:
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
        if normalized_query and documents:
            if strategy != "vector":
                index = BM25Index(documents, settings.knowledge_bm25_k1, settings.knowledge_bm25_b)
                keyword_scores = index.score(normalized_query)
            if strategy != "keyword":
                query_embedding = self.embedding_provider.embed_query(normalized_query)
                self.vector_store.ensure_collection(self.embedding_provider.dimensions)
                hits = self.vector_store.search(query_embedding, category, candidate_limit)
                for hit in hits:
                    # 忽略没有对应正文的旧向量；重复命中只保留最高分。
                    if hit.id in chunks_by_id:
                        previous_score = vector_scores.get(hit.id, 0.0)
                        vector_scores[hit.id] = max(previous_score, hit.score)
        keyword_ids = self._candidate_ids(keyword_scores, self.bm25_minimum_score, candidate_limit)
        vector_ids = self._candidate_ids(vector_scores, self.minimum_score, candidate_limit)
        keyword_ranks = self._ranks(keyword_ids)
        vector_ranks = self._ranks(vector_ids)
        if strategy == "keyword":
            scores = self._selected_scores(keyword_ids, keyword_scores)
            score_type = "bm25"
        elif strategy == "vector":
            scores = self._selected_scores(vector_ids, vector_scores)
            score_type = "cosine"
        else:
            scores = reciprocal_rank_fusion([keyword_ids, vector_ids], self.rrf_rank_constant)
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
        for hit in ranked[:limit]:
            rrf_score = None
            if hit.rrf_score is not None:
                rrf_score = round(hit.rrf_score, 6)
            items.append(KnowledgeSearchItem(
                document=hit.chunk.title, category=hit.chunk.category,
                section=hit.chunk.section, content=hit.chunk.content, source=hit.chunk.source,
                score=round(hit.score, 6), keyword_score=round(hit.keyword_score, 6),
                vector_score=round(hit.vector_score, 6), score_type=score_type,
                keyword_rank=hit.keyword_rank, vector_rank=hit.vector_rank, rrf_score=rrf_score,
            ))
        retrieval_mode = strategy
        if strategy in {"hybrid", "hybrid_rerank"} and not vector_ids:
            retrieval_mode = "keyword_fallback"
        return KnowledgeSearchResponse(
            query=query, items=items, total=len(items), retrieval_mode=retrieval_mode,
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
