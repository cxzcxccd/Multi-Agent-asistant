"""Supervisor 路由、复合任务和工具权限测试。"""

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.multi_agent.schemas import AgentName
from app.ai.multi_agent.supervisor import Supervisor
from app.ai.query_preprocessor import (
    IntentName,
    QueryAnalysis,
    QueryEntities,
)
from app.ai.runtime import AgentPermissionError, CustomerServiceRuntime
from tests.test_runtime import FakeModelClient, make_tool_call


def make_analysis(
    primary_intent: IntentName,
    secondary_intents: list[IntentName] | None = None,
) -> QueryAnalysis:
    """创建不依赖 Embedding 的稳定 Query 分析结果。"""

    return QueryAnalysis(
        original_query="测试问题",
        normalized_query="测试问题",
        routing_query="测试问题",
        optimized_query="测试问题",
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        similarity_score=0.9,
        route_margin=0.2,
        router_source="test",
        embedding_model="test",
        description="测试路由",
        entities=QueryEntities(),
    )


def test_supervisor_assigns_product_agent_with_minimum_tools() -> None:
    plan = Supervisor().plan(make_analysis(IntentName.PRODUCT_INQUIRY))

    assert plan.agents == [AgentName.PRODUCT]
    assert plan.primary_agent is AgentName.PRODUCT
    assert plan.allowed_tools == ["search_products", "get_product"]


def test_supervisor_combines_tools_for_compound_query() -> None:
    plan = Supervisor().plan(
        make_analysis(
            IntentName.ORDER_INQUIRY,
            [IntentName.AFTER_SALE],
        )
    )

    assert plan.agents == [AgentName.ORDER, AgentName.AFTER_SALES]
    assert plan.allowed_tools == [
        "list_orders",
        "get_order",
        "get_logistics",
        "list_after_sales",
        "get_after_sale",
        "prepare_after_sale_draft",
    ]


class FixedPreprocessor:
    """为权限测试返回固定订单意图。"""

    def analyze(self, _messages: object) -> QueryAnalysis:
        return make_analysis(IntentName.ORDER_INQUIRY)


def test_order_agent_cannot_call_product_tool() -> None:
    model_client = FakeModelClient(
        [AIMessage(content="", tool_calls=[make_tool_call(name="search_products")])]
    )
    runtime = CustomerServiceRuntime(
        model_client,
        query_preprocessor=FixedPreprocessor(),
    )

    try:
        runtime.invoke([HumanMessage(content="查询订单")])
    except AgentPermissionError as error:
        assert "order_agent" in str(error)
        assert "search_products" in str(error)
    else:
        raise AssertionError("订单 Agent 调用商品工具时应被拒绝")
