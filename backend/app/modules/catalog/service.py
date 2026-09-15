"""商品搜索、价格、库存和详情业务规则。"""

from app.modules.catalog.repository import ProductRepository
from app.modules.catalog.schemas import Product, ProductListResponse, ProductSearchParams


class ProductNotFoundError(LookupError):
    """请求的商品编号不存在。"""

    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        super().__init__(f"未找到商品：{product_id}")


class ProductService:
    """为商品接口和 AI 工具提供统一的只读查询规则。"""

    def __init__(self, repository: ProductRepository | None = None) -> None:
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
