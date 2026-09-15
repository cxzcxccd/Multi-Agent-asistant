"""商品 FastAPI 接口测试。"""

import asyncio
from collections.abc import Mapping
from typing import Any

from httpx import ASGITransport, AsyncClient, Response

from app.main import app


def api_get(path: str, params: Mapping[str, Any] | None = None) -> Response:
    """通过 ASGI 直接请求应用，不启动真实网络端口。"""

    async def request() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, params=params)

    return asyncio.run(request())


def test_list_products_returns_the_complete_catalog() -> None:
    response = api_get("/api/products")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 12
    assert len(body["items"]) == 12
    assert body["items"][0]["id"] == "p01"
    assert body["offset"] == 0
    assert body["limit"] == 20


def test_list_products_accepts_combined_filters() -> None:
    response = api_get(
        "/api/products",
        params={"category": "耳机", "max_price": 300, "in_stock": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert [product["id"] for product in body["items"]] == ["p01", "p04"]
    assert body["total"] == 2


def test_list_products_applies_keyword_sorting_and_pagination() -> None:
    response = api_get(
        "/api/products",
        params={"keyword": "扩展坞", "sort": "price-desc", "offset": 1, "limit": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert [product["id"] for product in body["items"]] == ["p10", "p09"]
    assert body["total"] == 4
    assert body["offset"] == 1
    assert body["limit"] == 2


def test_get_product_returns_product_details() -> None:
    response = api_get("/api/products/p01")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "p01"
    assert body["name"] == "AirBeat Pro 降噪耳机"
    assert body["category"] == "耳机"
    assert body["price"] == 299


def test_get_product_returns_404_for_an_unknown_product() -> None:
    response = api_get("/api/products/p99")

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到商品：p99"}


def test_get_product_rejects_an_invalid_product_id() -> None:
    response = api_get("/api/products/product-01")

    assert response.status_code == 422


def test_list_products_rejects_an_inverted_price_range() -> None:
    response = api_get(
        "/api/products",
        params={"min_price": 300, "max_price": 100},
    )

    assert response.status_code == 422


def test_list_products_rejects_unknown_query_parameters() -> None:
    response = api_get("/api/products", params={"discount": "true"})

    assert response.status_code == 422
