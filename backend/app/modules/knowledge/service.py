"""关键词与向量组合的混合知识检索服务。"""

import re

from app.core.config import settings
from app.modules.knowledge.embeddings import EmbeddingProvider, create_embedding_provider
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.schemas import (
    KnowledgeCategory,
    KnowledgeChunk,
    KnowledgeSearchItem,
    KnowledgeSearchResponse,
    KnowledgeIndexStatus,
    RetrievalStrategy,
)
from app.modules.knowledge.reranker import KnowledgeReranker
from app.modules.knowledge.vector_store import (
    KnowledgeVectorStore,
    VectorStoreUnavailableError,
    build_vector_id,
    create_knowledge_vector_store,
)

_latin_word_pattern = re.compile(r"[a-z0-9]+")
_chinese_pattern = re.compile(r"[\u4e00-\u9fff]+")


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: KnowledgeVectorStore | None = None,
        vector_weight: float | None = None,
        minimum_score: float | None = None,
        reranker: KnowledgeReranker | None = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider or create_embedding_provider()
        self.vector_store = vector_store or create_knowledge_vector_store()
        self.vector_weight = (
            settings.knowledge_vector_weight if vector_weight is None else vector_weight
        )
        self.minimum_score = (
            settings.knowledge_min_score if minimum_score is None else minimum_score
        )
        self.reranker = reranker

    def search(
        self,
        query: str,
        category: KnowledgeCategory | None = None,
        limit: int = 3,
        strategy: RetrievalStrategy = "hybrid",
    ) -> KnowledgeSearchResponse:
        normalized_query = query.strip().lower()
        query_terms = self._terms(normalized_query)
        vector_hits = []
        if strategy != "keyword":
            query_embedding = self.embedding_provider.embed_query(normalized_query)
            self.vector_store.ensure_collection(self.embedding_provider.dimensions)
            vector_limit = max(limit, settings.knowledge_vector_candidates)
            vector_hits = self.vector_store.search(query_embedding, category, vector_limit)
        vector_scores = {item.id: item.score for item in vector_hits}
        ranked: list[tuple[float, float, float, KnowledgeChunk]] = []
        vector_available = bool(vector_hits)

        for chunk in self.repository.list_chunks(category):
            searchable = f"{chunk.title} {chunk.section} {chunk.content}".lower()
            keyword_score = self._keyword_score(normalized_query, query_terms, searchable)
            vector_id = build_vector_id(chunk.document_key, chunk.position)
            vector_score = vector_scores.get(vector_id, 0.0)
            if strategy == "keyword" or not vector_available:
                final_score = keyword_score
            elif strategy == "vector":
                final_score = vector_score
            else:
                final_score = self._hybrid_score(keyword_score, vector_score)
            if final_score >= self.minimum_score:
                ranked.append((final_score, keyword_score, vector_score, chunk))

        ranked.sort(key=lambda item: (-item[0], item[3].document_key, item[3].position))
        if strategy == "hybrid_rerank":
            ranked = self._rerank(normalized_query, ranked)
        items: list[KnowledgeSearchItem] = []
        for score, keyword_score, vector_score, chunk in ranked[:limit]:
            items.append(
                KnowledgeSearchItem(
                    document=chunk.title,
                    category=chunk.category,
                    section=chunk.section,
                    content=chunk.content,
                    source=chunk.source,
                    score=round(score, 3),
                    keyword_score=round(keyword_score, 3),
                    vector_score=round(vector_score, 3),
                )
            )
        retrieval_mode = strategy if vector_available or strategy == "keyword" else "keyword_fallback"
        return KnowledgeSearchResponse(
            query=query,
            items=items,
            total=len(items),
            retrieval_mode=retrieval_mode,
        )

    def _rerank(
        self,
        query: str,
        ranked: list[tuple[float, float, float, KnowledgeChunk]],
    ) -> list[tuple[float, float, float, KnowledgeChunk]]:
        if self.reranker is None:
            raise ValueError("hybrid_rerank 检索需要配置重排模型")
        candidates = ranked[: settings.knowledge_vector_candidates]
        documents: list[str] = []
        for _, _, _, chunk in candidates:
            documents.append(f"{chunk.title}\n{chunk.section}\n{chunk.content}")
        scores = self.reranker.score(query, documents)
        reranked: list[tuple[float, float, float, KnowledgeChunk]] = []
        for candidate, rerank_score in zip(candidates, scores, strict=True):
            _, keyword_score, vector_score, chunk = candidate
            reranked.append((rerank_score, keyword_score, vector_score, chunk))
        reranked.sort(key=lambda item: (-item[0], item[3].document_key, item[3].position))
        return reranked

    def status(self) -> KnowledgeIndexStatus:
        """汇总关系数据库正文和 Milvus 向量集合的状态。"""

        chunks = self.repository.list_chunks()
        models = sorted({chunk.embedding_model for chunk in chunks if chunk.embedding_model})
        try:
            self.vector_store.ensure_collection(self.embedding_provider.dimensions)
            embedded_chunks = self.vector_store.count()
            connected = True
        except VectorStoreUnavailableError:
            embedded_chunks = 0
            connected = False
        return KnowledgeIndexStatus(
            chunks=len(chunks),
            embedded_chunks=embedded_chunks,
            embedding_models=models,
            collection_name=self.vector_store.collection_name,
            connected=connected,
        )

    def _hybrid_score(self, keyword_score: float, vector_score: float) -> float:
        vector_part = vector_score * self.vector_weight
        keyword_weight = 1 - self.vector_weight
        keyword_part = keyword_score * keyword_weight
        return vector_part + keyword_part

    @classmethod
    def _terms(cls, text: str) -> set[str]:
        terms = set(_latin_word_pattern.findall(text))
        for sequence in _chinese_pattern.findall(text):
            if len(sequence) == 1:
                terms.add(sequence)
                continue
            for index in range(len(sequence) - 1):
                terms.add(sequence[index : index + 2])
        return terms

    @classmethod
    def _keyword_score(cls, query: str, query_terms: set[str], searchable: str) -> float:
        document_terms = cls._terms(searchable)
        shared_terms = query_terms & document_terms
        if not shared_terms:
            return 0.0
        coverage = len(shared_terms) / max(len(query_terms), 1)
        exact_bonus = 0.25 if query and query in searchable else 0.0
        return min(coverage + exact_bonus, 1.0)
