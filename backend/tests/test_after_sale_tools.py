"""LangGraph 售后工具的身份隔离、只读查询和确认草稿测试。"""

import pytest

from app.ai.tools import after_sales
from app.ai.tools.after_sales import (
    get_after_sale_tools,
    prepare_after_sale_draft,
)
from app.ai.tools.orders import get_order_service


class EmptyAfterSaleService:
    """复用订单种子，但隔离用户本地可能已经创建的售后记录。"""

    def __init__(self) -> None:
        self.order_service = get_order_service()

    def list_for_buyer(self, _buyer_id: str) -> list[object]:
        return []


@pytest.fixture(autouse=True)
def isolated_after_sale_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = EmptyAfterSaleService()
    monkeypatch.setattr(after_sales, "get_after_sale_tool_service", lambda: service)


def buyer_config(buyer_id: str) -> dict[str, dict[str, str]]:
    """创建由会话服务注入的可信买家上下文。"""

    return {"configurable": {"buyer_id": buyer_id}}


def test_after_sale_tools_have_stable_names() -> None:
    tools = get_after_sale_tools()

    assert [item.name for item in tools] == [
        "list_after_sales",
        "get_after_sale",
        "prepare_after_sale_draft",
    ]
    assert set(prepare_after_sale_draft.args) == {
        "order_id",
        "request_type",
        "reason",
    }


def test_prepare_after_sale_draft_returns_confirmation_artifact_without_writing() -> None:
    result = prepare_after_sale_draft.invoke(
        {
            "order_id": "10002",
            "request_type": "退货",
            "reason": "左耳没有声音，已经更换设备测试",
        },
        config=buyer_config("A"),
    )

    assert result == {
        "success": True,
        "artifact": {
            "type": "after_sale_draft",
            "requires_confirmation": True,
            "draft": {
                "order_id": "10002",
                "request_type": "退货",
                "reason": "左耳没有声音，已经更换设备测试",
            },
        },
    }


def test_prepare_after_sale_draft_enforces_buyer_identity_and_order_status() -> None:
    foreign_order = prepare_after_sale_draft.invoke(
        {
            "order_id": "20001",
            "request_type": "退货",
            "reason": "商品出现故障，申请退货",
        },
        config=buyer_config("A"),
    )
    shipping_order = prepare_after_sale_draft.invoke(
        {
            "order_id": "10001",
            "request_type": "退货",
            "reason": "商品出现故障，申请退货",
        },
        config=buyer_config("A"),
    )

    assert foreign_order["error"]["code"] == "ORDER_NOT_ACCESSIBLE"
    assert shipping_order["error"]["code"] == "ORDER_NOT_ELIGIBLE"
