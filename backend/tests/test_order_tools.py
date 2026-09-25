"""LangGraph订单工具的身份隔离和结构化结果测试。"""

import pytest
from pydantic import ValidationError

from app.ai.tools.orders import (
    get_logistics,
    get_order,
    get_order_tools,
    list_orders,
)


def buyer_config(buyer_id: str) -> dict[str, dict[str, str]]:
    """创建由会话服务注入的工具运行配置。"""

    return {"configurable": {"buyer_id": buyer_id}}


def test_order_tools_have_stable_names_and_hide_buyer_argument() -> None:
    tools = get_order_tools()

    assert [item.name for item in tools] == [
        "list_orders",
        "get_order",
        "get_logistics",
    ]
    assert list_orders.args == {}
    assert set(get_order.args) == {"order_id"}
    assert set(get_logistics.args) == {"order_id"}


def test_list_orders_uses_buyer_from_runtime_config() -> None:
    buyer_a = list_orders.invoke({}, config=buyer_config("A"))
    buyer_b = list_orders.invoke({}, config=buyer_config("B"))

    assert buyer_a["success"] is True
    assert [order["id"] for order in buyer_a["orders"]] == [
        "10004",
        "10001",
        "10002",
        "10003",
    ]
    assert [order["id"] for order in buyer_b["orders"]] == ["20001"]


def test_get_logistics_returns_the_current_buyers_timeline() -> None:
    result = get_logistics.invoke(
        {"order_id": "10001"},
        config=buyer_config("A"),
    )

    assert result["success"] is True
    assert result["logistics"]["status"] == "运输中"
    assert len(result["logistics"]["events"]) == 3


def test_order_tool_does_not_reveal_a_foreign_order() -> None:
    foreign_order = get_order.invoke(
        {"order_id": "20001"},
        config=buyer_config("A"),
    )
    missing_order = get_order.invoke(
        {"order_id": "99999"},
        config=buyer_config("A"),
    )

    assert foreign_order == missing_order
    assert foreign_order["error"]["code"] == "ORDER_NOT_ACCESSIBLE"


def test_order_tools_reject_missing_buyer_context() -> None:
    result = get_order.invoke({"order_id": "10001"})

    assert result["success"] is False
    assert result["error"]["code"] == "BUYER_CONTEXT_REQUIRED"


@pytest.mark.parametrize("order_id", ["1", "order-10001", "100001"])
def test_order_tools_validate_order_id(order_id: str) -> None:
    with pytest.raises(ValidationError):
        get_order.invoke({"order_id": order_id}, config=buyer_config("A"))
