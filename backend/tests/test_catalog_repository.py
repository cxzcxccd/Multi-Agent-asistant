"""商品数据仓库的读取、校验和缓存测试。"""

import json
from pathlib import Path

import pytest

from app.modules.catalog.repository import ProductDataError, ProductRepository

PRODUCTS_PATH = Path(__file__).parents[1] / "data" / "seed" / "products.json"


def read_seed_records() -> list[dict[str, object]]:
    """读取测试使用的原始商品记录。"""

    return json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))


def write_records(path: Path, records: object) -> None:
    """以和正式模拟数据相同的编码写入临时测试文件。"""

    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def test_default_repository_loads_all_products_and_finds_by_id() -> None:
    repository = ProductRepository()

    products = repository.list_products()

    assert len(products) == 12
    assert repository.get_product("p01") == products[0]
    assert repository.get_product("p01").name == "AirBeat Pro 降噪耳机"
    assert repository.get_product("p99") is None


def test_repository_does_not_expose_mutable_cached_products() -> None:
    repository = ProductRepository()

    products = repository.list_products()
    products[0].name = "被调用方修改的名称"
    products[0].specs.append("被调用方修改的规格")

    original = repository.get_product("p01")
    assert original is not None
    assert original.name == "AirBeat Pro 降噪耳机"
    assert "被调用方修改的规格" not in original.specs


def test_repository_caches_data_until_cache_is_cleared(tmp_path: Path) -> None:
    data_path = tmp_path / "products.json"
    records = read_seed_records()[:1]
    write_records(data_path, records)
    repository = ProductRepository(data_path)

    assert repository.get_product("p01") is not None
    records[0]["name"] = "更新后的商品名称"
    write_records(data_path, records)
    assert repository.get_product("p01").name == "AirBeat Pro 降噪耳机"

    repository.clear_cache()
    assert repository.get_product("p01").name == "更新后的商品名称"


def test_repository_reports_a_missing_data_file(tmp_path: Path) -> None:
    repository = ProductRepository(tmp_path / "missing.json")

    with pytest.raises(ProductDataError, match="找不到商品数据文件"):
        repository.list_products()


def test_repository_reports_invalid_json(tmp_path: Path) -> None:
    data_path = tmp_path / "products.json"
    data_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ProductDataError, match="不是有效的 JSON"):
        ProductRepository(data_path).list_products()


def test_repository_requires_a_top_level_array(tmp_path: Path) -> None:
    data_path = tmp_path / "products.json"
    write_records(data_path, {"products": []})

    with pytest.raises(ProductDataError, match="顶层结构必须是数组"):
        ProductRepository(data_path).list_products()


def test_repository_reports_invalid_product_data(tmp_path: Path) -> None:
    data_path = tmp_path / "products.json"
    records = read_seed_records()[:1]
    records[0]["price"] = -1
    write_records(data_path, records)

    with pytest.raises(ProductDataError, match="商品数据格式校验失败"):
        ProductRepository(data_path).list_products()


def test_repository_rejects_duplicate_product_ids(tmp_path: Path) -> None:
    data_path = tmp_path / "products.json"
    records = read_seed_records()[:2]
    records[1]["id"] = records[0]["id"]
    write_records(data_path, records)

    with pytest.raises(ProductDataError, match="重复的商品编号"):
        ProductRepository(data_path).list_products()
