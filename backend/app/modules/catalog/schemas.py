"""商品目录的请求与响应数据格式。"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProductId = Annotated[str, StringConstraints(pattern=r"^p\d{2}$")]
ProductSort = Literal["featured", "price-asc", "price-desc"]


class ProductCategory(StrEnum):
    """数码旗舰店当前展示的商品分类。"""

    HEADPHONES = "耳机"
    CHARGERS = "充电器"
    HUBS = "扩展坞"


class Product(BaseModel):
    """由商品页面和 AI 商品工具共用的商品记录。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: ProductId
    name: NonBlankText
    category: ProductCategory
    series: NonBlankText
    price: int = Field(ge=0, description="当前演示价格，单位为人民币元")
    stock: int = Field(ge=0, description="当前演示库存数量")
    specs: list[NonBlankText] = Field(min_length=1)
    description: NonBlankText
    color: NonBlankText
    source: NonBlankText


class ProductSearchParams(BaseModel):
    """商品搜索服务和接口接受的筛选条件。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    keyword: str | None = Field(default=None, min_length=1, max_length=100)
    category: ProductCategory | None = None
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    in_stock: bool = False
    sort: ProductSort = "featured"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def validate_price_range(self) -> Self:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot be greater than max_price")
        return self


class ProductListResponse(BaseModel):
    """一页商品以及分页前的匹配商品总数。"""

    model_config = ConfigDict(extra="forbid")

    items: list[Product]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total < len(self.items):
            raise ValueError("total cannot be smaller than the number of returned items")
        return self
