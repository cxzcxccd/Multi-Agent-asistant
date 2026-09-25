"""在客服模型执行前规范化 Query、识别意图并提取业务实体。"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, settings
from app.modules.knowledge.embeddings import LocalHashEmbeddingProvider

LOGGER = logging.getLogger(__name__)
DEFAULT_ROUTES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "intents" / "routes.json"
)


class IntentName(StrEnum):
    """当前客服系统支持识别的业务意图。"""

    PRODUCT_INQUIRY = "product_inquiry"
    ORDER_INQUIRY = "order_inquiry"
    AFTER_SALE = "after_sale"
    KNOWLEDGE_INQUIRY = "knowledge_inquiry"
    HUMAN_SERVICE = "human_service"
    GENERAL_CONVERSATION = "general_conversation"


class QueryEntities(BaseModel):
    """从用户 Query 中确定性提取出的常用业务实体。"""

    model_config = ConfigDict(extra="forbid")

    order_id: str | None = None
    budget: float | None = Field(default=None, ge=0)
    product_category: str | None = None
    after_sale_type: str | None = None


class IntentCandidate(BaseModel):
    """一条候选意图及其向量相似度。"""

    model_config = ConfigDict(extra="forbid")

    intent: IntentName
    similarity_score: float = Field(ge=-1, le=1)


class QueryAnalysis(BaseModel):
    """一次 Query 预处理产生的稳定结构。"""

    model_config = ConfigDict(extra="forbid")

    original_query: str
    normalized_query: str
    routing_query: str
    optimized_query: str
    primary_intent: IntentName
    secondary_intents: list[IntentName] = Field(default_factory=list)
    similarity_score: float = Field(ge=-1, le=1)
    route_margin: float = Field(ge=0, le=2)
    router_source: str
    embedding_model: str
    description: str
    entities: QueryEntities
    candidates: list[IntentCandidate] = Field(default_factory=list)


class IntentEmbeddingProvider(Protocol):
    """语义路由需要的最小 Embedding 接口。"""

    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class FastEmbedIntentProvider:
    """使用本地 ONNX 中文模型生成语义向量。"""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for vector in self._model.embed(texts):
            vectors.append(vector.tolist())
        return vectors

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_documents([query])
        return vectors[0]


@dataclass(frozen=True, slots=True)
class IntentRoute:
    """一条语义路由和用于描述该意图的样本表达。"""

    name: IntentName
    description: str
    utterances: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteVector:
    """已经计算好向量的单条路由样本。"""

    intent: IntentName
    vector: tuple[float, ...]


class SemanticIntentRouter:
    """通过 Query 与路由样本的向量相似度识别业务意图。"""

    def __init__(
        self,
        embedding_provider: IntentEmbeddingProvider,
        routes: Sequence[IntentRoute],
        minimum_score: float,
        minimum_margin: float,
    ) -> None:
        if not routes:
            raise ValueError("语义路由至少需要一条意图定义")

        self.embedding_provider = embedding_provider
        self.routes = tuple(routes)
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self._descriptions = {route.name: route.description for route in routes}
        self._route_vectors = self._build_route_vectors()

    def route(self, query: str) -> tuple[
        IntentName,
        list[IntentName],
        float,
        float,
        list[IntentCandidate],
    ]:
        """返回主意图、次意图、最高分、分差和候选列表。"""

        query_vector = self.embedding_provider.embed_query(query)
        scores = self._score_routes(query_vector)
        candidates = self._sort_candidates(scores)

        best_candidate = candidates[0]
        second_score = candidates[1].similarity_score if len(candidates) > 1 else -1.0
        route_margin = max(0.0, best_candidate.similarity_score - second_score)

        score_is_sufficient = best_candidate.similarity_score >= self.minimum_score
        margin_is_sufficient = route_margin >= self.minimum_margin
        if not score_is_sufficient or not margin_is_sufficient:
            return (
                IntentName.GENERAL_CONVERSATION,
                [],
                best_candidate.similarity_score,
                route_margin,
                candidates,
            )

        return (
            best_candidate.intent,
            [],
            best_candidate.similarity_score,
            route_margin,
            candidates,
        )

    def description_for(self, intent: IntentName) -> str:
        """返回适合执行时间线展示的意图说明。"""

        if intent is IntentName.GENERAL_CONVERSATION:
            return "当前问题未命中明确业务路由，将按普通对话处理。"
        return self._descriptions[intent]

    def _build_route_vectors(self) -> tuple[RouteVector, ...]:
        utterances: list[str] = []
        intents: list[IntentName] = []

        for route in self.routes:
            if not route.utterances:
                raise ValueError(f"意图 {route.name} 缺少路由样本")
            for utterance in route.utterances:
                utterances.append(utterance)
                intents.append(route.name)

        vectors = self.embedding_provider.embed_documents(utterances)
        if len(vectors) != len(utterances):
            raise ValueError("Embedding 返回的路由向量数量不正确")

        route_vectors: list[RouteVector] = []
        for intent, vector in zip(intents, vectors, strict=True):
            route_vectors.append(
                RouteVector(
                    intent=intent,
                    vector=tuple(vector),
                )
            )
        return tuple(route_vectors)

    def _score_routes(self, query_vector: list[float]) -> dict[IntentName, float]:
        scores: dict[IntentName, float] = {}

        for route_vector in self._route_vectors:
            score = cosine_similarity(query_vector, route_vector.vector)
            previous_score = scores.get(route_vector.intent)
            if previous_score is None or score > previous_score:
                scores[route_vector.intent] = score
        return scores

    @staticmethod
    def _sort_candidates(scores: dict[IntentName, float]) -> list[IntentCandidate]:
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        candidates: list[IntentCandidate] = []
        for intent, score in ordered:
            candidates.append(
                IntentCandidate(
                    intent=intent,
                    similarity_score=round(score, 4),
                )
            )
        return candidates

class QueryPreprocessor:
    """组合文本规范化、上下文补全、语义路由和实体提取。"""

    def __init__(self, intent_router: SemanticIntentRouter) -> None:
        self.intent_router = intent_router

    def analyze(self, messages: Sequence[BaseMessage]) -> QueryAnalysis:
        """分析最后一条用户消息，并返回后续节点可直接使用的结果。"""

        latest_message = messages[-1]
        original_query = str(latest_message.content)
        normalized_query = normalize_query(original_query)
        routing_query = build_routing_query(messages, normalized_query)

        (
            primary_intent,
            _,
            similarity_score,
            route_margin,
            candidates,
        ) = self.intent_router.route(routing_query)

        clause_intents = self._route_compound_query(normalized_query)
        secondary_intents = merge_secondary_intents(
            primary_intent,
            clause_intents,
        )

        entities = extract_entities(normalized_query, primary_intent)
        optimized_query = optimize_query(
            normalized_query,
            primary_intent,
            secondary_intents,
            entities,
        )

        return QueryAnalysis(
            original_query=original_query,
            normalized_query=normalized_query,
            routing_query=routing_query,
            optimized_query=optimized_query,
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            similarity_score=round(similarity_score, 4),
            route_margin=round(route_margin, 4),
            router_source="semantic_router",
            embedding_model=self.intent_router.embedding_provider.model_name,
            description=self.intent_router.description_for(primary_intent),
            entities=entities,
            candidates=candidates,
        )

    def _route_compound_query(self, query: str) -> list[IntentName]:
        """对明确的复合分句分别路由，用于发现真正的次要意图。"""

        clauses = split_query_clauses(query)
        if len(clauses) <= 1:
            return []

        intents: list[IntentName] = []
        for clause in clauses:
            intent, _, _, _, _ = self.intent_router.route(clause)
            if intent is IntentName.GENERAL_CONVERSATION:
                continue
            if intent not in intents:
                intents.append(intent)
        return intents


def normalize_query(query: str) -> str:
    """统一字符宽度并删除不参与语义判断的空白和控制字符。"""

    normalized = unicodedata.normalize("NFKC", query)
    visible_characters: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("C") and character not in {"\n", "\t"}:
            continue
        visible_characters.append(character)

    visible_text = "".join(visible_characters)
    return re.sub(r"\s+", " ", visible_text).strip()


def build_routing_query(
    messages: Sequence[BaseMessage],
    normalized_query: str,
) -> str:
    """只在当前问题依赖上文时，为语义路由补充最近一轮上下文。"""

    if not query_needs_context(normalized_query):
        return normalized_query

    historical_messages = list(messages[:-1])
    recent_context: list[str] = []
    for message in reversed(historical_messages):
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        content = normalize_query(str(message.content))
        if not content:
            continue
        role = "用户" if isinstance(message, HumanMessage) else "客服"
        recent_context.append(f"{role}：{content[:160]}")
        if len(recent_context) == 2:
            break

    if not recent_context:
        return normalized_query

    recent_context.reverse()
    context_text = "\n".join(recent_context)
    return f"最近对话：\n{context_text}\n当前问题：{normalized_query}"


def query_needs_context(query: str) -> bool:
    """判断当前 Query 是否含有明显的省略或指代表达。"""

    reference_pattern = re.compile(
        r"^(那|这个|那个|它|这种|这款|上一款|刚才|然后)|"
        r"(呢|怎么样|还有吗|可以吗|多久)$"
    )
    only_identifier = bool(re.fullmatch(r"[A-Za-z0-9-]{2,20}", query))
    return only_identifier or bool(reference_pattern.search(query))


def split_query_clauses(query: str) -> list[str]:
    """按标点和连接词拆分复合请求，保留每个可独立判断的分句。"""

    separator = re.compile(
        r"[，,；;。]+|(?<=\S)(?:以及|并且|同时|然后|再)(?=\S)"
    )
    clauses: list[str] = []
    for part in separator.split(query):
        clause = part.strip()
        if len(clause) >= 2:
            clauses.append(clause)
    return clauses


def merge_secondary_intents(
    primary_intent: IntentName,
    clause_intents: Sequence[IntentName],
) -> list[IntentName]:
    """从复合分句结果中排除主意图与重复项。"""

    secondary: list[IntentName] = []
    for intent in clause_intents:
        if intent is primary_intent or intent in secondary:
            continue
        secondary.append(intent)
    return secondary


def extract_entities(query: str, intent: IntentName) -> QueryEntities:
    """用确定性规则提取订单号、预算、商品类别和售后类型。"""

    order_id = extract_order_id(query, intent)
    budget = extract_budget(query)
    product_category = extract_product_category(query)
    after_sale_type = extract_after_sale_type(query)
    return QueryEntities(
        order_id=order_id,
        budget=budget,
        product_category=product_category,
        after_sale_type=after_sale_type,
    )


def extract_order_id(query: str, intent: IntentName) -> str | None:
    explicit_match = re.search(r"订单(?:号|编号)?\s*[:：#-]?\s*(\d{5})", query)
    if explicit_match:
        return explicit_match.group(1)

    order_related_intents = {IntentName.ORDER_INQUIRY, IntentName.AFTER_SALE}
    if intent in order_related_intents:
        standalone_match = re.search(r"(?<!\d)(\d{5})(?!\d)", query)
        if standalone_match:
            return standalone_match.group(1)
    return None


def extract_budget(query: str) -> float | None:
    budget_pattern = re.compile(
        r"(?:预算|不超过|最多|低于|小于)"
        r"\s*(?:是|为|在)?\s*[￥¥]?\s*(\d+(?:\.\d+)?)\s*元?"
    )
    match = budget_pattern.search(query)
    if not match:
        amount_first = re.search(
            r"[￥¥]?\s*(\d+(?:\.\d+)?)\s*元?\s*(?:以内|以下)",
            query,
        )
        if amount_first:
            return float(amount_first.group(1))
        currency_first = re.search(r"[￥¥]\s*(\d+(?:\.\d+)?)", query)
        if not currency_first:
            return None
        match = currency_first
    return float(match.group(1))


def extract_product_category(query: str) -> str | None:
    for category in ("耳机", "充电器", "扩展坞"):
        if category in query:
            return category
    return None


def extract_after_sale_type(query: str) -> str | None:
    if "换货" in query or "换一个" in query:
        return "换货"
    if "退货" in query or "退款" in query or "退掉" in query:
        return "退货"
    return None


def optimize_query(
    query: str,
    primary_intent: IntentName,
    secondary_intents: Sequence[IntentName],
    entities: QueryEntities,
) -> str:
    """将路由结果和已确认实体整理成模型可读的业务 Query。"""

    details: list[str] = [f"用户原意：{query}", f"主要意图：{primary_intent.value}"]
    if secondary_intents:
        intent_names = "、".join(intent.value for intent in secondary_intents)
        details.append(f"次要意图：{intent_names}")
    if entities.order_id:
        details.append(f"订单号：{entities.order_id}")
    if entities.budget is not None:
        details.append(f"预算上限：{entities.budget:g} 元")
    if entities.product_category:
        details.append(f"商品类别：{entities.product_category}")
    if entities.after_sale_type:
        details.append(f"售后类型：{entities.after_sale_type}")
    return "；".join(details)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """计算两个同维向量的余弦相似度。"""

    if len(left) != len(right):
        raise ValueError("Embedding 向量维度不一致")
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if left_length == 0 or right_length == 0:
        return 0.0
    return dot_product / (left_length * right_length)


@lru_cache(maxsize=1)
def load_intent_routes(path: Path = DEFAULT_ROUTES_PATH) -> tuple[IntentRoute, ...]:
    """从数据文件读取并缓存意图路由样本。"""

    records = json.loads(path.read_text(encoding="utf-8"))
    routes: list[IntentRoute] = []
    for record in records:
        routes.append(
            IntentRoute(
                name=IntentName(record["name"]),
                description=str(record["description"]),
                utterances=tuple(str(item) for item in record["utterances"]),
            )
        )
    return tuple(routes)


def create_fallback_query_preprocessor() -> QueryPreprocessor:
    """创建无需下载模型的轻量预处理器，供测试与异常降级使用。"""

    provider = LocalHashEmbeddingProvider(
        dimensions=512,
        model_name="local-hash-intent-fallback",
    )
    router = SemanticIntentRouter(
        embedding_provider=provider,
        routes=load_intent_routes(),
        minimum_score=0.25,
        minimum_margin=0.0,
    )
    return QueryPreprocessor(router)


def create_query_preprocessor(
    configuration: Settings = settings,
) -> QueryPreprocessor:
    """根据配置创建本地语义预处理器，模型不可用时自动降级。"""

    provider_name = configuration.intent_embedding_provider.strip().lower()
    if provider_name == "fastembed":
        try:
            provider: IntentEmbeddingProvider = FastEmbedIntentProvider(
                model_name=configuration.intent_embedding_model,
                cache_dir=configuration.intent_embedding_cache_dir or None,
            )
        except Exception:
            LOGGER.exception("本地意图 Embedding 加载失败，已降级为特征哈希路由")
            return create_fallback_query_preprocessor()
    else:
        return create_fallback_query_preprocessor()

    router = SemanticIntentRouter(
        embedding_provider=provider,
        routes=load_intent_routes(),
        minimum_score=configuration.intent_route_min_score,
        minimum_margin=configuration.intent_route_min_margin,
    )
    return QueryPreprocessor(router)
