"""供大模型调用的商品搜索与商品详情工具。"""

from functools import lru_cache
from typing import Any, Literal, Self

from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.catalog.repository import SqlProductRepository
from app.modules.catalog.schemas import (
    Product,
    ProductCategory,
    ProductId,
    ProductSearchParams,
    ProductSort,
)
from app.modules.catalog.service import ProductNotFoundError, ProductService


class SearchProductsInput(BaseModel):
    """大模型搜索商品时可提供的参数。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keyword: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="商品名称、系列、规格或用途关键词",
    )
    category: ProductCategory | None = Field(
        default=None,
        description="商品分类，只能是耳机、充电器或扩展坞",
    )
    min_price: int | None = Field(
        default=None,
        ge=0,
        description="最低价格，单位为人民币元",
    )
    max_price: int | None = Field(
        default=None,
        ge=0,
        description="最高价格，单位为人民币元",
    )
    in_stock: bool = Field(default=False, description="是否只返回当前有货商品")
    sort: ProductSort = Field(
        default="default",
        description="排序方式：默认顺序、价格升序或价格降序",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="最多返回多少件商品，范围为 1 到 10",
    )

    @model_validator(mode="after")
    def validate_price_range(self) -> Self:
        """确保价格区间方向正确。"""

        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("最低价格不能高于最高价格")
        return self


class GetProductInput(BaseModel):
    """大模型查询单件商品详情时提供的参数。"""

    model_config = ConfigDict(extra="forbid")

    product_id: ProductId = Field(description="商品编号，例如 p01")


class SearchProductsResult(BaseModel):
    """商品搜索工具返回的结构化结果。"""

    success: Literal[True] = True
    items: list[Product]
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    has_more: bool


class ToolError(BaseModel):
    """可由模型识别和解释的工具错误。"""

    code: Literal["PRODUCT_NOT_FOUND"]
    message: str


class GetProductResult(BaseModel):
    """商品详情工具返回的结构化结果。"""

    success: bool
    product: Product | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        """确保成功结果和错误结果不会同时出现。"""

        if self.success and (self.product is None or self.error is not None):
            raise ValueError("成功结果必须包含商品且不能包含错误")
        if not self.success and (self.product is not None or self.error is None):
            raise ValueError("失败结果必须包含错误且不能包含商品")
        return self


@lru_cache(maxsize=1)
def get_product_service() -> ProductService:
    """延迟创建数据库商品服务，避免导入模块时执行迁移。"""

    initialize_database()
    repository = SqlProductRepository(get_session_factory())
    return ProductService(repository)


@tool("search_products", args_schema=SearchProductsInput)
def search_products(
    keyword: str | None = None,
    category: ProductCategory | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    in_stock: bool = False,
    sort: ProductSort = "default",
    limit: int = 5,
) -> dict[str, Any]:
    """搜索店内商品。需要推荐、比较、查询价格库存或按条件选购时使用。"""

    service = get_product_service()
    result = service.search_products(
        ProductSearchParams(
            keyword=keyword,
            category=category,
            min_price=min_price,
            max_price=max_price,
            in_stock=in_stock,
            sort=sort,
            offset=0,
            limit=limit,
        )
    )
    response = SearchProductsResult(
        items=result.items,
        total=result.total,
        returned=len(result.items),
        has_more=result.total > len(result.items),
    )
    return response.model_dump(mode="json")


@tool("get_product", args_schema=GetProductInput)
def get_product(product_id: str) -> dict[str, Any]:
    """按商品编号查询完整详情。用户追问某件商品的规格或库存时使用。"""

    try:
        service = get_product_service()
        product = service.get_product(product_id)
    except ProductNotFoundError as exc:
        return GetProductResult(
            success=False,
            error=ToolError(code="PRODUCT_NOT_FOUND", message=str(exc)),
        ).model_dump(mode="json")

    return GetProductResult(success=True, product=product).model_dump(mode="json")


CATALOG_TOOLS: tuple[BaseTool, ...] = (search_products, get_product)


def get_catalog_tools() -> list[BaseTool]:
    """返回可安全交给模型绑定的商品工具列表。"""

    return list(CATALOG_TOOLS)
