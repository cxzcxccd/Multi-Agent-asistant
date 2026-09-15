"""从本地模拟数据文件读取商品。"""

import json
from functools import cached_property
from json import JSONDecodeError
from pathlib import Path

from pydantic import ValidationError

from app.modules.catalog.schemas import Product

DEFAULT_PRODUCTS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "seed" / "products.json"
)


class ProductDataError(RuntimeError):
    """商品数据文件无法读取或未通过校验。"""


class ProductRepository:
    """提供只读商品数据，并缓存已校验的数据快照。"""

    def __init__(self, data_path: Path | str = DEFAULT_PRODUCTS_PATH) -> None:
        self.data_path = Path(data_path)

    @cached_property
    def _products(self) -> tuple[Product, ...]:
        try:
            raw_data = self.data_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ProductDataError(f"找不到商品数据文件：{self.data_path}") from exc
        except OSError as exc:
            raise ProductDataError(f"无法读取商品数据文件：{self.data_path}") from exc

        try:
            records = json.loads(raw_data)
        except JSONDecodeError as exc:
            raise ProductDataError(f"商品数据文件不是有效的 JSON：{self.data_path}") from exc

        if not isinstance(records, list):
            raise ProductDataError("商品数据文件的顶层结构必须是数组")

        try:
            products = tuple(Product.model_validate(record) for record in records)
        except ValidationError as exc:
            raise ProductDataError(f"商品数据格式校验失败：{exc}") from exc

        product_ids = [product.id for product in products]
        if len(product_ids) != len(set(product_ids)):
            raise ProductDataError("商品数据中存在重复的商品编号")

        return products

    @cached_property
    def _products_by_id(self) -> dict[str, Product]:
        return {product.id: product for product in self._products}

    def list_products(self) -> list[Product]:
        """按数据文件中的陈列顺序返回全部商品。"""

        return [product.model_copy(deep=True) for product in self._products]

    def get_product(self, product_id: str) -> Product | None:
        """按商品编号查询；不存在时返回空值。"""

        product = self._products_by_id.get(product_id)
        return product.model_copy(deep=True) if product is not None else None

    def clear_cache(self) -> None:
        """清除数据快照，使下一次查询重新读取文件。"""

        self.__dict__.pop("_products", None)
        self.__dict__.pop("_products_by_id", None)
