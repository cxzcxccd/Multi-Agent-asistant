"""订单、订单明细和物流接口的数据格式。"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.catalog.schemas import ProductId
from app.modules.conversations.schemas import BuyerId

OrderId = Annotated[str, StringConstraints(pattern=r"^\d{5}$")]
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OrderStatus(StrEnum):
    """当前演示订单允许出现的状态。"""

    PENDING_SHIPMENT = "待发货"
    IN_TRANSIT = "运输中"
    DELIVERED = "已签收"
    COMPLETED = "已完成"


class OrderItem(BaseModel):
    """订单中的商品和下单价格快照。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: ProductId
    product_name: NonBlankText
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0, decimal_places=2)


class LogisticsEvent(BaseModel):
    """一条已经确认的物流轨迹。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    occurred_at: AwareDatetime
    title: NonBlankText
    detail: NonBlankText


class Order(BaseModel):
    """买家有权查看的一笔完整订单。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: OrderId
    buyer_id: BuyerId
    status: OrderStatus
    ordered_at: date
    carrier: str | None = None
    tracking_number: str | None = None
    items: list[OrderItem] = Field(min_length=1)
    logistics: list[LogisticsEvent] = Field(default_factory=list)
    total_amount: Decimal = Field(ge=0, decimal_places=2)


class OrderListResponse(BaseModel):
    """当前买家可见的订单列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[Order]
    total: int = Field(ge=0)


class LogisticsResponse(BaseModel):
    """订单物流承运信息和时间线。"""

    model_config = ConfigDict(extra="forbid")

    order_id: OrderId
    status: OrderStatus
    carrier: str | None = None
    tracking_number: str | None = None
    events: list[LogisticsEvent]


class OrderSeed(BaseModel):
    """版本库中一笔固定演示订单的导入格式。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: OrderId
    buyer_id: BuyerId
    status: OrderStatus
    ordered_at: date
    carrier: str | None = None
    tracking_number: str | None = None
    items: list[OrderItem] = Field(min_length=1)
    logistics: list[LogisticsEvent] = Field(default_factory=list)
