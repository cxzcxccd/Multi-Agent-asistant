"""从本地模拟数据文件读取商品。"""

import json
import re
from functools import cached_property
from json import JSONDecodeError
from pathlib import Path
from collections.abc import Callable

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.catalog.models import ProductRecord
from app.modules.catalog.schemas import (
    Product,
    StockOperation,
)

DEFAULT_PRODUCTS_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "seed" / "products.json"
)


class ProductDataError(RuntimeError):
    """商品数据文件无法读取或未通过校验。"""


class ProductAlreadyExistsError(ProductDataError):
    """待创建的商品编号已存在。"""


class InsufficientStockError(ProductDataError):
    """出库数量超过当前库存，会导致负库存。"""

    def __init__(self, product_id: str, current_stock: int, requested: int) -> None:
        self.product_id = product_id
        self.current_stock = current_stock
        self.requested = requested
        super().__init__(
            f"商品 {product_id} 库存不足：当前 {current_stock}，请求扣减 {requested}"
        )


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


class SqlProductRepository:
    """使用 SQLAlchemy 读取已迁移到数据库的商品。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_products(self) -> list[Product]:
        """按商品编号返回全部商品。"""

        with self._session_factory() as session:
            statement = select(ProductRecord).order_by(ProductRecord.id)
            records = session.scalars(statement).all()
            products: list[Product] = []
            for record in records:
                products.append(self._to_schema(record))
            return products

    def get_product(self, product_id: str) -> Product | None:
        """按商品编号查询；不存在时返回空值。"""

        with self._session_factory() as session:
            record = session.get(ProductRecord, product_id)
            if record is None:
                return None
            return self._to_schema(record)

    @staticmethod
    def _to_schema(record: ProductRecord) -> Product:
        """把数据库记录转换成稳定的商品响应格式。"""

        return Product(
            id=record.id,
            name=record.name,
            category=record.category,
            series=record.series,
            price=record.price,
            stock=record.stock,
            specs=list(record.specs),
            description=record.description,
            color=record.color,
            source=record.source,
            image_url=record.image_url,
        )

    def exists(self, product_id: str) -> bool:
        """判断商品编号是否已存在。"""

        with self._session_factory() as session:
            return session.get(ProductRecord, product_id) is not None

    def next_product_id(self) -> str:
        """按现有最大编号分配下一个 pNN 编号。"""

        with self._session_factory() as session:
            product_ids = session.scalars(select(ProductRecord.id)).all()

        highest = 0
        for product_id in product_ids:
            match = re.fullmatch(r"p(\d{2})", product_id)
            if match is not None:
                highest = max(highest, int(match.group(1)))
        if highest >= 99:
            raise ProductDataError("商品编号已达 p99 上限，无法继续自动分配")
        return f"p{highest + 1:02d}"

    def insert(self, product: Product) -> Product:
        """插入新商品；编号冲突时抛出异常并回滚。"""

        with self._session_factory() as session:
            record = ProductRecord(
                id=product.id,
                name=product.name,
                category=product.category,
                series=product.series,
                price=product.price,
                stock=product.stock,
                specs=list(product.specs),
                description=product.description,
                color=product.color,
                source=product.source,
                image_url=product.image_url,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ProductAlreadyExistsError(
                    f"商品编号已存在：{product.id}"
                ) from exc
            session.refresh(record)
            return self._to_schema(record)

    def update_fields(
        self, product_id: str, changes: dict[str, object]
    ) -> Product | None:
        """按字段部分更新；商品不存在时返回空值。"""

        with self._session_factory() as session:
            record = session.get(ProductRecord, product_id)
            if record is None:
                return None
            for key, value in changes.items():
                if key == "category" and hasattr(value, "value"):
                    value = value.value
                if key == "specs" and value is not None:
                    value = list(value)
                setattr(record, key, value)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ProductDataError(f"商品更新失败：{product_id}") from exc
            session.refresh(record)
            return self._to_schema(record)

    def adjust_stock(
        self, product_id: str, operation: StockOperation, quantity: int
    ) -> Product:
        """使用数据库原子更新盘点、入库或出库，并阻止负库存。"""

        with self._session_factory() as session:
            if operation == "set":
                statement = (
                    update(ProductRecord)
                    .where(ProductRecord.id == product_id)
                    .values(stock=quantity)
                )
            elif operation == "add":
                statement = (
                    update(ProductRecord)
                    .where(ProductRecord.id == product_id)
                    .values(stock=ProductRecord.stock + quantity)
                )
            else:
                statement = (
                    update(ProductRecord)
                    .where(ProductRecord.id == product_id)
                    .where(ProductRecord.stock >= quantity)
                    .values(stock=ProductRecord.stock - quantity)
                )

            result = session.execute(statement)
            if result.rowcount == 0:
                record = session.get(ProductRecord, product_id)
                if record is None:
                    raise LookupError(f"未找到商品：{product_id}")
                raise InsufficientStockError(product_id, record.stock, quantity)

            session.commit()
            record = session.get(ProductRecord, product_id)
            if record is None:
                raise LookupError(f"未找到商品：{product_id}")
            session.refresh(record)
            return self._to_schema(record)

    def delete(self, product_id: str) -> bool:
        """删除商品；商品不存在时返回 False。"""

        with self._session_factory() as session:
            record = session.get(ProductRecord, product_id)
            if record is None:
                return False
            session.delete(record)
            session.commit()
            return True
