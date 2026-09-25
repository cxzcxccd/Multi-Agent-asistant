"""知识检索使用的本地 BGE、特征哈希与 OpenAI Embedding Provider。"""

import hashlib
import math
import re
from typing import Any, Protocol

from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings, settings

_term_pattern = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
_synonyms = {
    "进水": "进液",
    "泡水": "进液",
    "免费维修": "保修",
    "修理": "维修",
    "退钱": "退款",
    "快递": "物流",
    "几天到": "送达时间",
}


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class LocalHashEmbeddingProvider:
    """离线可复现的特征哈希向量，供本地开发和降级使用。"""

    def __init__(self, dimensions: int = 256, model_name: str = "local-hash-v1") -> None:
        self._dimensions = dimensions
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(self._embed(text))
        return vectors

    def embed_query(self, query: str) -> list[float]:
        return self._embed(query)

    def _embed(self, text: str) -> list[float]:
        normalized = text.lower()
        for source, target in _synonyms.items():
            normalized = normalized.replace(source, target)
        tokens = _term_pattern.findall(normalized)
        features = list(tokens)
        for index in range(len(tokens) - 1):
            features.append(tokens[index] + tokens[index + 1])

        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            position = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[position] += sign
        length = math.sqrt(sum(value * value for value in vector))
        if length == 0:
            return vector
        return [value / length for value in vector]


class FastEmbedEmbeddingProvider:
    """通过 FastEmbed 在本地运行真实的 BGE 中文向量模型。"""

    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        model: Any | None = None,
        dimensions: int | None = None,
    ) -> None:
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._dimensions = dimensions or TextEmbedding.get_embedding_size(model_name)
        self._model = model or TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for vector in self._model.passage_embed(texts):
            values = vector.tolist()
            self._validate_dimensions(values)
            vectors.append(values)
        return vectors

    def embed_query(self, query: str) -> list[float]:
        vectors = self._model.query_embed([query])
        vector = next(iter(vectors))
        values = vector.tolist()
        self._validate_dimensions(values)
        return values

    def _validate_dimensions(self, vector: list[float]) -> None:
        if len(vector) != self.dimensions:
            raise ValueError(
                f"模型 {self.model_name} 返回 {len(vector)} 维向量，"
                f"但模型元数据声明为 {self.dimensions} 维"
            )


class OpenAIEmbeddingProvider:
    def __init__(self, configuration: Settings) -> None:
        api_key = configuration.model_api_key
        if api_key is None or not api_key.get_secret_value():
            raise ValueError("使用 OpenAI Embedding 时必须配置 MODEL_API_KEY")
        self._model_name = configuration.embedding_model
        self._dimensions = configuration.embedding_dimensions
        self._client = OpenAIEmbeddings(
            model=configuration.embedding_model,
            api_key=api_key.get_secret_value(),
            base_url=configuration.model_base_url or None,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, query: str) -> list[float]:
        return self._client.embed_query(query)


def create_embedding_provider(configuration: Settings = settings) -> EmbeddingProvider:
    provider_name = configuration.embedding_provider.strip().lower()
    if provider_name == "fastembed":
        return FastEmbedEmbeddingProvider(
            model_name=configuration.embedding_model,
            cache_dir=configuration.embedding_cache_dir or None,
        )
    if provider_name == "openai":
        return OpenAIEmbeddingProvider(configuration)
    if provider_name == "local":
        return LocalHashEmbeddingProvider(
            dimensions=configuration.embedding_dimensions,
            model_name=configuration.embedding_model,
        )
    raise ValueError(f"不支持的知识 Embedding Provider：{configuration.embedding_provider}")
