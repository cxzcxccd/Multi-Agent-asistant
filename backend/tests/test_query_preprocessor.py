"""Query 规范化、语义意图路由和实体提取测试。"""

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.query_preprocessor import (
    IntentName,
    IntentRoute,
    QueryPreprocessor,
    SemanticIntentRouter,
    build_routing_query,
    extract_entities,
    normalize_query,
    optimize_query,
)


class FixedEmbeddingProvider:
    """使用预设二维向量，隔离真实模型下载和数值波动。"""

    model_name = "fixed-test-embedding"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vectors.append(self.vectors[text])
        return vectors

    def embed_query(self, query: str) -> list[float]:
        return self.vectors[query]


def make_preprocessor() -> QueryPreprocessor:
    """创建只有商品和订单两个意图的可预测预处理器。"""

    routes = [
        IntentRoute(
            name=IntentName.PRODUCT_INQUIRY,
            description="用户正在咨询商品。",
            utterances=("推荐耳机",),
        ),
        IntentRoute(
            name=IntentName.ORDER_INQUIRY,
            description="用户正在查询订单。",
            utterances=("查询订单",),
        ),
    ]
    vectors = {
        "推荐耳机": [1.0, 0.0],
        "查询订单": [0.0, 1.0],
        "帮我查询订单10001": [0.1, 0.99],
        "帮我查询订单10001,再推荐耳机": [0.9, 0.4],
        "今天天气如何": [-0.7, -0.7],
    }
    provider = FixedEmbeddingProvider(vectors)
    router = SemanticIntentRouter(
        embedding_provider=provider,
        routes=routes,
        minimum_score=0.7,
        minimum_margin=0.1,
    )
    return QueryPreprocessor(router)


def test_normalize_query_removes_control_characters_and_extra_whitespace() -> None:
    normalized = normalize_query("  帮我\u200b  查询\n订单 １０００１  ")

    assert normalized == "帮我 查询 订单 10001"


def test_context_dependent_query_uses_recent_conversation() -> None:
    messages = [
        HumanMessage(content="极客蓝牙耳机还有库存吗"),
        AIMessage(content="目前黑色款有库存。"),
        HumanMessage(content="那白色的呢"),
    ]

    routing_query = build_routing_query(messages, "那白色的呢")

    assert "极客蓝牙耳机" in routing_query
    assert "目前黑色款有库存" in routing_query
    assert "当前问题：那白色的呢" in routing_query


def test_self_contained_query_does_not_append_history() -> None:
    messages = [
        HumanMessage(content="之前聊过耳机"),
        AIMessage(content="好的"),
        HumanMessage(content="请查询订单10001的物流信息"),
    ]

    routing_query = build_routing_query(messages, "请查询订单10001的物流信息")

    assert routing_query == "请查询订单10001的物流信息"

    greeting_messages = [
        HumanMessage(content="之前聊过耳机"),
        AIMessage(content="好的"),
        HumanMessage(content="你好"),
    ]
    assert build_routing_query(greeting_messages, "你好") == "你好"


def test_semantic_router_recognizes_order_and_extracts_order_id() -> None:
    preprocessor = make_preprocessor()

    analysis = preprocessor.analyze([HumanMessage(content="帮我查询订单10001")])

    assert analysis.primary_intent is IntentName.ORDER_INQUIRY
    assert analysis.similarity_score > 0.9
    assert analysis.entities.order_id == "10001"
    assert analysis.embedding_model == "fixed-test-embedding"
    assert "订单号：10001" in analysis.optimized_query


def test_semantic_router_rejects_query_below_threshold() -> None:
    preprocessor = make_preprocessor()

    analysis = preprocessor.analyze([HumanMessage(content="今天天气如何")])

    assert analysis.primary_intent is IntentName.GENERAL_CONVERSATION
    assert analysis.secondary_intents == []


def test_compound_query_keeps_a_secondary_intent() -> None:
    preprocessor = make_preprocessor()

    analysis = preprocessor.analyze(
        [HumanMessage(content="帮我查询订单10001，再推荐耳机")]
    )

    assert analysis.primary_intent is IntentName.PRODUCT_INQUIRY
    assert analysis.secondary_intents == [IntentName.ORDER_INQUIRY]


def test_extract_entities_and_optimize_product_query() -> None:
    entities = extract_entities(
        "推荐500元以内的耳机",
        IntentName.PRODUCT_INQUIRY,
    )
    optimized = optimize_query(
        "推荐500元以内的耳机",
        IntentName.PRODUCT_INQUIRY,
        [],
        entities,
    )

    assert entities.budget == 500
    assert entities.product_category == "耳机"
    assert "预算上限：500 元" in optimized
    assert "商品类别：耳机" in optimized
