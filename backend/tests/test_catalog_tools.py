"""AI 商品工具的输入、输出和业务结果测试。"""

import json

import pytest
from pydantic import ValidationError

from app.ai.tools.catalog import get_catalog_tools, get_product, search_products


def test_catalog_tools_have_stable_names_and_chinese_descriptions() -> None:
    tools = get_catalog_tools()

    assert [item.name for item in tools] == ["search_products", "get_product"]
    assert "搜索店内商品" in tools[0].description
    assert "查询完整详情" in tools[1].description


def test_search_products_returns_matching_in_stock_headphones() -> None:
    result = search_products.invoke(
        {"category": "耳机", "max_price": 300, "in_stock": True}
    )

    assert result["success"] is True
    assert [product["id"] for product in result["items"]] == ["p01", "p04"]
    assert result["total"] == 2
    assert result["returned"] == 2
    assert result["has_more"] is False


def test_search_products_limits_model_context_and_reports_more_matches() -> None:
    result = search_products.invoke({"category": "充电器", "limit": 2})

    assert [product["id"] for product in result["items"]] == ["p05", "p06"]
    assert result["total"] == 4
    assert result["returned"] == 2
    assert result["has_more"] is True


def test_search_products_returns_an_empty_successful_result() -> None:
    result = search_products.invoke({"keyword": "不存在的商品"})

    assert result == {
        "success": True,
        "items": [],
        "total": 0,
        "returned": 0,
        "has_more": False,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"category": "手机"},
        {"max_price": -1},
        {"min_price": 300, "max_price": 100},
        {"limit": 11},
        {"unknown": "value"},
    ],
)
def test_search_products_rejects_invalid_arguments(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        search_products.invoke(arguments)


def test_get_product_returns_complete_product_details() -> None:
    result = get_product.invoke({"product_id": "p01"})

    assert result["success"] is True
    assert result["error"] is None
    assert result["product"]["name"] == "AirBeat Pro 降噪耳机"
    assert result["product"]["price"] == 299
    assert result["product"]["source"].startswith("KB-P01")


def test_get_product_returns_a_structured_not_found_error() -> None:
    result = get_product.invoke({"product_id": "p99"})

    assert result == {
        "success": False,
        "product": None,
        "error": {"code": "PRODUCT_NOT_FOUND", "message": "未找到商品：p99"},
    }


def test_tool_results_can_be_serialized_as_json() -> None:
    search_result = search_products.invoke({"keyword": "AirBeat"})
    detail_result = get_product.invoke({"product_id": "p01"})

    assert "AirBeat" in json.dumps(search_result, ensure_ascii=False)
    assert "p01" in json.dumps(detail_result, ensure_ascii=False)
