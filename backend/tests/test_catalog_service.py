"""商品查询业务规则测试。"""

import pytest

from app.modules.catalog.schemas import ProductCategory, ProductSearchParams
from app.modules.catalog.service import ProductNotFoundError, ProductService


def test_search_without_filters_preserves_catalog_order() -> None:
    result = ProductService().search_products()

    assert result.total == 12
    assert [product.id for product in result.items] == [
        "p01",
        "p02",
        "p03",
        "p04",
        "p05",
        "p06",
        "p07",
        "p08",
        "p09",
        "p10",
        "p11",
        "p12",
    ]


@pytest.mark.parametrize(
    ("keyword", "expected_ids"),
    [
        ("airbeat", ["p01", "p03"]),
        ("65w", ["p05"]),
        ("仅数据传输", ["p11"]),
        ("不存在的商品", []),
    ],
)
def test_keyword_search_matches_storefront_fields(
    keyword: str, expected_ids: list[str]
) -> None:
    result = ProductService().search_products(ProductSearchParams(keyword=keyword))

    assert [product.id for product in result.items] == expected_ids
    assert result.total == len(expected_ids)


def test_search_combines_category_budget_and_stock_filters() -> None:
    params = ProductSearchParams(
        category=ProductCategory.HEADPHONES,
        max_price=300,
        in_stock=True,
    )

    result = ProductService().search_products(params)

    assert [product.id for product in result.items] == ["p01", "p04"]
    assert all(product.price <= 300 for product in result.items)
    assert all(product.stock > 0 for product in result.items)


@pytest.mark.parametrize(
    ("sort", "expected_ids"),
    [
        ("price-asc", ["p07", "p11", "p05"]),
        ("price-desc", ["p12", "p02", "p08"]),
    ],
)
def test_search_sorts_products_by_price(sort: str, expected_ids: list[str]) -> None:
    params = ProductSearchParams(sort=sort, limit=3)

    result = ProductService().search_products(params)

    assert [product.id for product in result.items] == expected_ids
    assert result.total == 12


def test_search_paginates_after_filtering_and_sorting() -> None:
    params = ProductSearchParams(sort="price-asc", offset=2, limit=3)

    result = ProductService().search_products(params)

    assert [product.id for product in result.items] == ["p05", "p03", "p09"]
    assert result.total == 12
    assert result.offset == 2
    assert result.limit == 3


def test_get_product_returns_a_known_product() -> None:
    product = ProductService().get_product("p01")

    assert product.name == "AirBeat Pro 降噪耳机"


def test_get_product_raises_a_business_error_for_an_unknown_id() -> None:
    with pytest.raises(ProductNotFoundError, match="未找到商品：p99") as error:
        ProductService().get_product("p99")

    assert error.value.product_id == "p99"
