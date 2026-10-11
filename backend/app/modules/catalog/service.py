"""商品搜索、价格、库存和详情业务规则。"""

from typing import Protocol, cast

from app.modules.catalog.repository import (
    InsufficientStockError,
    ProductAlreadyExistsError,
    ProductRepository,
)
from app.modules.catalog.schemas import (
    Product,
    ProductCreate,
    ProductListResponse,
    ProductSearchParams,
    ProductUpdate,
    StockAdjustment,
    StockOperation,
)


class ProductReader(Protocol):
    """商品服务依赖的最小只读仓库接口。"""

    def list_products(self) -> list[Product]: ...

    def get_product(self, product_id: str) -> Product | None: ...


class ProductStore(ProductReader, Protocol):
    """写操作依赖的可读写仓库接口。"""

    def next_product_id(self) -> str: ...

    def insert(self, product: Product) -> Product: ...

    def update_fields(
        self, product_id: str, changes: dict[str, object]
    ) -> Product | None: ...

    def adjust_stock(
        self, product_id: str, operation: StockOperation, quantity: int
    ) -> Product: ...

    def delete(self, product_id: str) -> bool: ...


class ProductNotFoundError(LookupError):
    """请求的商品编号不存在。"""

    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        super().__init__(f"未找到商品：{product_id}")


class ProductService:
    """为商品接口和 AI 工具提供统一的只读查询规则。"""

    def __init__(self, repository: ProductReader | None = None) -> None:
        self.repository = repository or ProductRepository()

    def search_products(
        self, params: ProductSearchParams | None = None
    ) -> ProductListResponse:
        """根据筛选、排序和分页条件查询商品。"""

        search = params or ProductSearchParams()
        products = self.repository.list_products()

        if search.keyword is not None:
            keyword = search.keyword.casefold()
            products = [
                product
                for product in products
                if keyword
                in " ".join(
                    [
                        product.name,
                        product.series,
                        *product.specs,
                        product.description,
                    ]
                ).casefold()
            ]

        if search.category is not None:
            products = [
                product for product in products if product.category == search.category
            ]

        if search.min_price is not None:
            products = [
                product for product in products if product.price >= search.min_price
            ]

        if search.max_price is not None:
            products = [
                product for product in products if product.price <= search.max_price
            ]

        if search.in_stock:
            products = [product for product in products if product.stock > 0]

        if search.sort == "price-asc":
            products.sort(key=lambda product: (product.price, product.id))
        elif search.sort == "price-desc":
            products.sort(key=lambda product: (-product.price, product.id))

        total = len(products)
        page = products[search.offset : search.offset + search.limit]
        return ProductListResponse(
            items=page,
            total=total,
            offset=search.offset,
            limit=search.limit,
        )

    def get_product(self, product_id: str) -> Product:
        """按商品编号返回商品，不存在时抛出业务异常。"""

        product = self.repository.get_product(product_id)
        if product is None:
            raise ProductNotFoundError(product_id)
        return product

    def create_product(self, data: ProductCreate) -> Product:
        """创建商品并自动分配编号。"""

        store = cast(ProductStore, self.repository)
        product_id = store.next_product_id()
        product = Product(id=product_id, **data.model_dump())
        return store.insert(product)

    def update_product(self, product_id: str, data: ProductUpdate) -> Product:
        """部分更新商品字段，商品不存在时抛出业务异常。"""

        store = cast(ProductStore, self.repository)
        changes = data.model_dump(exclude_unset=True)
        product = store.update_fields(product_id, changes)
        if product is None:
            raise ProductNotFoundError(product_id)
        return product

    def adjust_stock(
        self, product_id: str, adjustment: StockAdjustment
    ) -> Product:
        """盘点、入库或出库；库存不足时抛出业务异常。"""

        store = cast(ProductStore, self.repository)
        try:
            return store.adjust_stock(
                product_id, adjustment.operation, adjustment.quantity
            )
        except LookupError as exc:
            raise ProductNotFoundError(product_id) from exc

    def delete_product(self, product_id: str) -> None:
        """删除商品，商品不存在时抛出业务异常。"""

        store = cast(ProductStore, self.repository)
        if not store.delete(product_id):
            raise ProductNotFoundError(product_id)
