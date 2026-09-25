"""订单和物流 FastAPI 接口测试。"""

import asyncio
from collections.abc import Mapping
from typing import Any

from httpx import ASGITransport, AsyncClient, Response

from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token


BUYER_TOKEN = create_access_token(
    Principal(subject="buyer_a", display_name="林同学", role="buyer", buyer_id="A")
)


def api_get(path: str, params: Mapping[str, Any] | None = None) -> Response:
    """通过 ASGI 直接请求应用。"""

    async def request() -> Response:
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {BUYER_TOKEN}"}
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            return await client.get(path, params=params)

    return asyncio.run(request())


def test_list_orders_only_returns_current_buyer_orders() -> None:
    response = api_get("/api/orders", params={"buyer_id": "A"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [order["id"] for order in body["items"]] == [
        "10004",
        "10001",
        "10002",
        "10003",
    ]
    assert all(order["buyer_id"] == "A" for order in body["items"])


def test_get_order_returns_items_and_price_snapshot() -> None:
    response = api_get("/api/orders/10001", params={"buyer_id": "A"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "运输中"
    assert body["total_amount"] == "299.00"
    assert body["items"] == [
        {
            "product_id": "p01",
            "product_name": "AirBeat Pro 降噪耳机",
            "quantity": 1,
            "unit_price": "299.00",
        }
    ]


def test_get_logistics_returns_confirmed_timeline() -> None:
    response = api_get("/api/orders/10001/logistics", params={"buyer_id": "A"})

    assert response.status_code == 200
    body = response.json()
    assert body["carrier"] == "演示速运"
    assert body["tracking_number"] == "DEMO10001"
    assert [event["title"] for event in body["events"]] == [
        "运输中",
        "已揽收",
        "商家已发货",
    ]


def test_pending_order_returns_an_empty_logistics_timeline() -> None:
    response = api_get("/api/orders/10004/logistics", params={"buyer_id": "A"})

    assert response.status_code == 200
    assert response.json()["status"] == "待发货"
    assert response.json()["events"] == []


def test_foreign_and_missing_orders_return_the_same_public_error() -> None:
    foreign_order = api_get("/api/orders/20001", params={"buyer_id": "A"})
    missing_order = api_get("/api/orders/99999", params={"buyer_id": "A"})

    expected_body = {"detail": "未找到可查询的订单，请核对或联系人工"}
    assert foreign_order.status_code == 404
    assert missing_order.status_code == 404
    assert foreign_order.json() == expected_body
    assert missing_order.json() == expected_body


def test_order_endpoints_validate_identity_and_order_id() -> None:
    authenticated_buyer = api_get("/api/orders", params={"buyer_id": "C"})
    invalid_order = api_get("/api/orders/order-1", params={"buyer_id": "A"})

    assert authenticated_buyer.status_code == 200
    assert invalid_order.status_code == 422
