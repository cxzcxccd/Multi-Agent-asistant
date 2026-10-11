"""商品目录的请求与响应数据格式。"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# 必须是一个字符串，并且去掉前后空格后，长度至少为 1。
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
#必须是字符串，并且必须符合 p + 两位数字 的格式。
ProductId = Annotated[str, StringConstraints(pattern=r"^p\d{2}$")]
#这个值只能是指定的三个字符串之一
ProductSort = Literal["default", "price-asc", "price-desc"]


class ProductCategory(StrEnum):
    """数码旗舰店当前展示的商品分类。"""

    HEADPHONES = "耳机"
    CHARGERS = "充电器"
    HUBS = "扩展坞"


class Product(BaseModel):
    """由商品页面和 AI 商品工具共用的商品记录。"""
    # 禁止用户传入未定义字段，并且所有字符串自动去掉首尾空格。
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
    sort: ProductSort = "default"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)

    # 当 Pydantic 已经把所有字段都验证完成以后，再额外检查一次：min_price 是否小于等于 max_price
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


class ProductCreate(BaseModel):
    """管理员创建商品的请求；编号由系统自动分配。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: NonBlankText
    category: ProductCategory
    series: NonBlankText
    price: int = Field(ge=0, description="价格，单位为人民币元")
    stock: int = Field(ge=0, description="初始库存数量")
    specs: list[NonBlankText] = Field(min_length=1)
    description: NonBlankText
    color: NonBlankText
    source: NonBlankText = Field(default="手动录入")


class ProductUpdate(BaseModel):
    """管理员部分更新商品资料；库存必须通过专用接口调整。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: NonBlankText | None = None
    category: ProductCategory | None = None
    series: NonBlankText | None = None
    price: int | None = Field(default=None, ge=0)
    specs: list[NonBlankText] | None = Field(default=None, min_length=1)
    description: NonBlankText | None = None
    color: NonBlankText | None = None
    source: NonBlankText | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_empty_or_null_update(cls, data: object) -> object:
        """拒绝没有修改内容的请求，也不允许把必填字段显式设为空。"""

        if not isinstance(data, dict):
            return data
        if not data:
            raise ValueError("至少需要提供一个待更新字段")

        null_fields: list[str] = []
        for field_name, value in data.items():
            if value is None:
                null_fields.append(field_name)
        if null_fields:
            field_names = "、".join(null_fields)
            raise ValueError(f"商品字段不能设为 null：{field_names}")
        return data


# 库存调整方式：设为绝对值、入库增加、出库扣减。
StockOperation = Literal["set", "add", "subtract"]


class StockAdjustment(BaseModel):
    """库存盘点、入库或出库的请求。"""

    model_config = ConfigDict(extra="forbid")

    operation: StockOperation
    quantity: int = Field(ge=0, description="数量必须为非负整数")
