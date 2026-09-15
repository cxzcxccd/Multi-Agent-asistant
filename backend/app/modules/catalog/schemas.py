"""Validated request and response schemas for the product catalog."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProductId = Annotated[str, StringConstraints(pattern=r"^p\d{2}$")]
ProductSort = Literal["featured", "price-asc", "price-desc"]


class ProductCategory(StrEnum):
    """Categories currently displayed by the digital storefront."""

    HEADPHONES = "耳机"
    CHARGERS = "充电器"
    HUBS = "扩展坞"


class Product(BaseModel):
    """A product record shared by the storefront and the AI catalog tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: ProductId
    name: NonBlankText
    category: ProductCategory
    series: NonBlankText
    price: int = Field(ge=0, description="Current demo price in CNY yuan")
    stock: int = Field(ge=0, description="Current demo stock quantity")
    specs: list[NonBlankText] = Field(min_length=1)
    description: NonBlankText
    color: NonBlankText
    source: NonBlankText


class ProductSearchParams(BaseModel):
    """Validated filters accepted by product search services and routes."""

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
    """A page of products and the number of matches before pagination."""

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
