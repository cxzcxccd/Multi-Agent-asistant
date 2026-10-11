"""管理员商品增删改查与库存调整的接口测试。

使用临时 SQLite 数据库并通过 dependency_overrides 覆盖商品服务，
不会修改或污染本地 app.db。
"""

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.db.base import Base
from app.main import app
from app.modules.auth.schemas import Principal
from app.modules.auth.security import create_access_token
from app.modules.catalog.repository import InsufficientStockError, SqlProductRepository
from app.modules.catalog.router import get_product_service
from app.modules.catalog.schemas import Product, ProductCategory, StockAdjustment
from app.modules.catalog.service import (
    ProductNotFoundError,
    ProductService,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ADMIN_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(subject="admin", display_name="管理员", role="admin")
    )
}
BUYER_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(
            subject="buyer_a", display_name="林同学", role="buyer", buyer_id="A"
        )
    )
}
STAFF_HEADERS = {
    "Authorization": "Bearer "
    + create_access_token(
        Principal(subject="staff", display_name="客服小周", role="staff")
    )
}


def make_product(
    product_id: str, name: str, stock: int, price: int = 199
) -> Product:
    """构造一条合法商品。"""

    return Product(
        id=product_id,
        name=name,
        category=ProductCategory.HEADPHONES,
        series="TEST",
        price=price,
        stock=stock,
        specs=["规格 A"],
        description="测试商品",
        color="white",
        source="测试数据",
    )


@pytest.fixture
def product_service(tmp_path: Path) -> AsyncIterator[ProductService]:
    """搭建带两条种子商品的临时服务，并覆盖应用依赖。"""

    db_path = tmp_path / "catalog_admin.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    repository = SqlProductRepository(factory)
    repository.insert(make_product("p01", "测试耳机一号", stock=20, price=299))
    repository.insert(make_product("p02", "测试耳机二号", stock=0, price=159))
    service = ProductService(repository)

    app.dependency_overrides[get_product_service] = lambda: service
    yield service
    app.dependency_overrides.clear()


def api_request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    json: Any = None,
) -> Response:
    """通过 ASGI 发起一次请求。"""

    async def request() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.request(
                method, path, headers=headers, json=json
            )

    return asyncio.run(request())


NEW_PRODUCT = {
    "name": "新品耳机",
    "category": "耳机",
    "series": "NEW",
    "price": 259,
    "stock": 50,
    "specs": ["蓝牙 5.4"],
    "description": "新上架商品",
    "color": "black",
}


def test_admin_can_create_a_product(product_service: ProductService) -> None:
    response = api_request(
        "POST", "/api/products", headers=ADMIN_HEADERS, json=NEW_PRODUCT
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "p03"
    assert body["name"] == "新品耳机"
    assert body["source"] == "手动录入"
    assert product_service.get_product("p03").stock == 50


def test_create_product_rejects_invalid_payload(
    product_service: ProductService,
) -> None:
    payload = dict(NEW_PRODUCT)
    payload["price"] = -1

    response = api_request(
        "POST", "/api/products", headers=ADMIN_HEADERS, json=payload
    )

    assert response.status_code == 422


def test_admin_can_partially_update_a_product(
    product_service: ProductService,
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p01",
        headers=ADMIN_HEADERS,
        json={"price": 188, "name": "改名后的耳机"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["price"] == 188
    assert body["name"] == "改名后的耳机"
    # 未传入的字段保持不变
    assert body["stock"] == 20


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"stock": 10}])
def test_update_rejects_invalid_or_stock_fields(
    product_service: ProductService,
    payload: dict[str, object],
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p01",
        headers=ADMIN_HEADERS,
        json=payload,
    )

    assert response.status_code == 422
    assert product_service.get_product("p01").name == "测试耳机一号"
    assert product_service.get_product("p01").stock == 20


def test_update_unknown_product_returns_404(
    product_service: ProductService,
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p99",
        headers=ADMIN_HEADERS,
        json={"price": 1},
    )

    assert response.status_code == 404


def test_stock_add_set_and_subtract(product_service: ProductService) -> None:
    added = api_request(
        "PATCH",
        "/api/products/p01/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "add", "quantity": 10},
    )
    assert added.status_code == 200
    assert added.json()["stock"] == 30

    set_value = api_request(
        "PATCH",
        "/api/products/p01/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "set", "quantity": 7},
    )
    assert set_value.json()["stock"] == 7

    subtracted = api_request(
        "PATCH",
        "/api/products/p01/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "subtract", "quantity": 5},
    )
    assert subtracted.json()["stock"] == 2


def test_stock_subtract_rejects_overselling(
    product_service: ProductService,
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p01/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "subtract", "quantity": 100},
    )

    assert response.status_code == 409
    assert "库存不足" in response.json()["detail"]


def test_concurrent_stock_subtraction_never_oversells(
    product_service: ProductService,
) -> None:
    """并发出库时，每次扣减必须是原子的，成功次数不能超过实际库存。"""

    adjustment = StockAdjustment(operation="subtract", quantity=1)

    def subtract_once() -> bool:
        try:
            product_service.adjust_stock("p01", adjustment)
            return True
        except InsufficientStockError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _index: subtract_once(), range(25)))

    assert outcomes.count(True) == 20
    assert outcomes.count(False) == 5
    assert product_service.get_product("p01").stock == 0


def test_stock_adjustment_rejects_negative_quantity(
    product_service: ProductService,
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p01/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "add", "quantity": -5},
    )

    assert response.status_code == 422


def test_stock_adjustment_unknown_product_returns_404(
    product_service: ProductService,
) -> None:
    response = api_request(
        "PATCH",
        "/api/products/p99/stock",
        headers=ADMIN_HEADERS,
        json={"operation": "set", "quantity": 1},
    )

    assert response.status_code == 404


def test_admin_can_delete_a_product(product_service: ProductService) -> None:
    response = api_request(
        "DELETE", "/api/products/p02", headers=ADMIN_HEADERS
    )

    assert response.status_code == 204
    with pytest.raises(ProductNotFoundError):
        product_service.get_product("p02")
    assert api_request("GET", "/api/products/p02").status_code == 404


def test_delete_unknown_product_returns_404(
    product_service: ProductService,
) -> None:
    response = api_request(
        "DELETE", "/api/products/p99", headers=ADMIN_HEADERS
    )

    assert response.status_code == 404


def test_write_operations_require_admin(
    product_service: ProductService,
) -> None:
    assert (
        api_request("POST", "/api/products", json=NEW_PRODUCT).status_code
        == 401
    )
    assert (
        api_request(
            "POST",
            "/api/products",
            headers=BUYER_HEADERS,
            json=NEW_PRODUCT,
        ).status_code
        == 403
    )
    assert (
        api_request(
            "POST",
            "/api/products",
            headers=STAFF_HEADERS,
            json=NEW_PRODUCT,
        ).status_code
        == 403
    )
    assert (
        api_request(
            "DELETE",
            "/api/products/p01",
            headers=BUYER_HEADERS,
        ).status_code
        == 403
    )


def test_product_list_remains_public(
    product_service: ProductService,
) -> None:
    response = api_request("GET", "/api/products")

    assert response.status_code == 200
    assert response.json()["total"] == 2
