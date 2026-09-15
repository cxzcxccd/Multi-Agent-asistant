"""Validation tests for product catalog contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.catalog.schemas import (
    Product,
    ProductCategory,
    ProductListResponse,
    ProductSearchParams,
)

PRODUCTS_PATH = Path(__file__).parents[1] / "data" / "seed" / "products.json"


def test_all_seed_products_match_the_product_contract() -> None:
    records = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))

    products = [Product.model_validate(record) for record in records]

    assert len(products) == 12
    assert len({product.id for product in products}) == 12
    assert {product.category for product in products} == set(ProductCategory)
    assert products[0].model_dump(mode="json")["category"] == "耳机"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("id", "product-01"),
        ("price", -1),
        ("stock", -1),
        ("specs", []),
    ],
)
def test_product_rejects_invalid_business_data(field: str, invalid_value: object) -> None:
    record = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))[0]
    record[field] = invalid_value

    with pytest.raises(ValidationError):
        Product.model_validate(record)


def test_product_rejects_unknown_fields() -> None:
    record = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))[0]
    record["discount"] = 0.8

    with pytest.raises(ValidationError):
        Product.model_validate(record)


def test_search_params_validate_and_normalize_filters() -> None:
    params = ProductSearchParams(
        keyword="  AirBeat  ",
        category="耳机",
        min_price=100,
        max_price=300,
        in_stock=True,
        sort="price-asc",
    )

    assert params.keyword == "AirBeat"
    assert params.category is ProductCategory.HEADPHONES
    assert params.limit == 20


def test_search_params_reject_an_inverted_price_range() -> None:
    with pytest.raises(ValidationError, match="min_price cannot be greater"):
        ProductSearchParams(min_price=300, max_price=100)


def test_product_list_response_rejects_an_impossible_total() -> None:
    record = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))[0]

    with pytest.raises(ValidationError, match="total cannot be smaller"):
        ProductListResponse(
            items=[Product.model_validate(record)],
            total=0,
            offset=0,
            limit=20,
        )
