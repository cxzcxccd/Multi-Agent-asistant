"""RAG 候选知识块的交叉编码重排。"""

import math
from typing import Any, Protocol

from app.core.config import settings


class KnowledgeReranker(Protocol):
    def score(self, query: str, documents: list[str]) -> list[float]: ...


class FastEmbedReranker:
    """使用 FastEmbed 的 BGE Cross Encoder 计算 Query 与文档的相关度。"""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        model: Any | None = None,
    ) -> None:
        if model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            model = TextCrossEncoder(
                model_name=model_name,
                cache_dir=settings.embedding_cache_dir or None,
            )
        self.model = model
        self.model_name = model_name

    def score(self, query: str, documents: list[str]) -> list[float]:
        raw_scores = self.model.rerank(query, documents)
        scores: list[float] = []
        for raw_score in raw_scores:
            value = float(raw_score)
            scores.append(self._sigmoid(value))
        return scores

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            return 1 / (1 + math.exp(-value))
        exponential = math.exp(value)
        return exponential / (1 + exponential)
