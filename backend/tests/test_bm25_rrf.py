"""验证 BM25 的统计性质、RRF 排名融合和服务的候选筛选。"""

import math

import pytest

from app.modules.knowledge.bm25 import BM25Index, tokenize
from app.modules.knowledge.fusion import reciprocal_rank_fusion
from app.modules.knowledge.schemas import KnowledgeChunk
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.vector_store import VectorSearchHit, VectorStoreUnavailableError


def test_bm25_matches_formula_and_preserves_term_frequency() -> None:
    index = BM25Index({"a": "refund refund", "b": "charger"})
    scores = index.score("refund")
    expected_idf = math.log(2)
    expected_tf = 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 2 / 1.5))
    assert scores["a"] == pytest.approx(expected_idf * expected_tf)
    assert scores["b"] == 0
    assert tokenize("退款退款 USB65W") == ["usb65w", "退款", "款退", "退款"]


def test_bm25_rewards_rare_terms_and_penalizes_long_documents() -> None:
    index = BM25Index({"a": "common rare", "b": "common common"})
    assert index.score("rare")["a"] > index.score("common")["a"]
    index = BM25Index({"short": "refund", "long": "refund " + "charger " * 100})
    assert index.score("refund")["short"] > index.score("refund")["long"]
    index = BM25Index({"single": "refund", "many": "refund " * 100}, b=0)
    scores = index.score("refund")
    assert scores["single"] < scores["many"] < 2.2 * scores["single"]


def test_bm25_handles_empty_corpus_and_unknown_queries() -> None:
    assert BM25Index({}).score("refund") == {}
    assert BM25Index({"empty": ""}).score("refund") == {"empty": 0}
    assert BM25Index({"a": "refund"}).score("unknown") == {"a": 0}
    assert BM25Index({"a": "refund"}).score("") == {"a": 0}


def test_rrf_rewards_agreement_and_counts_duplicate_hits_once() -> None:
    scores = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["b"] > scores["a"] > scores["c"]
    assert reciprocal_rank_fusion([["a", "a", "b"], []]) == reciprocal_rank_fusion([["a", "b"]])
    assert reciprocal_rank_fusion([[], []]) == {}
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], rank_constant=0)


class StubRepository:
    def __init__(self) -> None:
        self.chunks = [
            KnowledgeChunk(document_key="a", position=0, title="Policy", section="A",
                           category="return_policy", content="refund " * 10, source="a.md#A"),
            KnowledgeChunk(document_key="b", position=0, title="Policy", section="B",
                           category="return_policy", content="refund charger", source="b.md#B"),
            KnowledgeChunk(document_key="c", position=0, title="Policy", section="C",
                           category="shipping_policy", content="charger", source="c.md#C"),
        ]

    def list_chunks(self, category=None):
        selected = []
        for chunk in self.chunks:
            if category is None or chunk.category == category:
                selected.append(chunk)
        return selected


class StubEmbedding:
    dimensions = 1

    def embed_query(self, query):
        return [1.0]


class StubVectors:
    def __init__(self, hits):
        self.hits = hits

    def ensure_collection(self, dimensions):
        pass

    def search(self, vector, category, limit):
        return self.hits[:limit]


def create_service(hits):
    return KnowledgeService(StubRepository(), StubEmbedding(), StubVectors(hits))


def test_service_uses_rank_fusion_and_filters_stale_or_low_score_vectors() -> None:
    service = create_service([
        VectorSearchHit("stale:0", 0.99), VectorSearchHit("b:0", 0.9),
        VectorSearchHit("c:0", 0.8), VectorSearchHit("b:0", 0.7), VectorSearchHit("a:0", 0.01),
    ])
    result = service.search("refund")
    assert result.items[0].source == "b.md#B"
    assert len(result.items) == 3
    assert result.items[0].score_type == "rrf"
    for item in result.items:
        expected = 0
        if item.keyword_rank is not None:
            expected += 1 / (60 + item.keyword_rank)
        if item.vector_rank is not None:
            expected += 1 / (60 + item.vector_rank)
        assert item.score == pytest.approx(expected, abs=0.000001)
        assert item.rrf_score == item.score
    assert result.items[0].score < 0.32  # RRF 结果不再被旧的混合分数阈值丢弃。


def test_keyword_does_not_call_embeddings_and_vector_does_not_fall_back_to_bm25() -> None:
    class ForbiddenEmbedding:
        def embed_query(self, query):
            raise AssertionError("纯 BM25 不应调用向量模型")

    service = create_service([])
    service.embedding_provider = ForbiddenEmbedding()
    keyword = service.search("refund", strategy="keyword")
    assert keyword.items[0].score_type == "bm25"
    service.embedding_provider = StubEmbedding()
    assert service.search("refund", strategy="vector").items == []
    assert service.search("refund").retrieval_mode == "keyword_fallback"
    assert service.search("astronomy").items == []
    assert service.search("  ").items == []


def test_empty_rerank_does_not_call_model_and_outages_are_not_silent_fallbacks() -> None:
    class ForbiddenReranker:
        def score(self, query, documents):
            raise AssertionError("空候选不应调用重排模型")

    service = create_service([])
    service.reranker = ForbiddenReranker()
    assert service.search("astronomy", strategy="hybrid_rerank").items == []

    class UnavailableVectors(StubVectors):
        def search(self, vector, category, limit):
            raise VectorStoreUnavailableError("离线")

    service.vector_store = UnavailableVectors([])
    with pytest.raises(VectorStoreUnavailableError):
        service.search("refund")


def test_search_ignores_provenance_urls_and_refreshes_bm25_on_content_changes() -> None:
    service = create_service([])
    first = service.repository.chunks[0]
    first.content = "refund\n原文：https://example.com/uniquetoken\n采集时间：2026-10-09"
    assert service.search("uniquetoken", strategy="keyword").items == []
    assert service.search("newterm", strategy="keyword").items == []
    first.content = "newterm"
    assert service.search("newterm", strategy="keyword").items[0].source == "a.md#A"


def test_rerank_keeps_fusion_scores_and_original_ranks() -> None:
    class ReverseReranker:
        def score(self, query, documents):
            scores = []
            for index, _document in enumerate(documents):
                scores.append((index + 1) / len(documents))
            return scores

    service = create_service([VectorSearchHit("b:0", 0.9), VectorSearchHit("c:0", 0.8)])
    service.reranker = ReverseReranker()
    result = service.search("refund", strategy="hybrid_rerank")
    assert result.items[0].score == 1.0
    assert result.items[0].score_type == "rerank"
    assert result.items[0].rrf_score is not None
    assert result.items[0].keyword_rank is not None or result.items[0].vector_rank is not None
