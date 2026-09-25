"""售后草稿、提交、审核和操作记录的数据格式。"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.conversations.schemas import BuyerId
from app.modules.orders.schemas import OrderId

ReasonText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=5, max_length=500),
]


class AfterSaleType(StrEnum):
    """当前演示支持的售后申请类型。"""

    RETURN = "退货"
    EXCHANGE = "换货"


class AfterSaleStatus(StrEnum):
    """售后申请的确定性状态。"""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AfterSaleOperation(BaseModel):
    """一条可审计的售后操作记录。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    actor_type: Literal["buyer", "staff", "system"]
    actor_id: str
    detail: str
    created_at: AwareDatetime


class AfterSaleRequest(BaseModel):
    """返回给买家或客服的完整售后申请。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    buyer_id: BuyerId
    order_id: OrderId
    request_type: AfterSaleType
    reason: ReasonText
    status: AfterSaleStatus
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    submitted_at: AwareDatetime | None = None
    reviewed_at: AwareDatetime | None = None
    reviewer: str | None = None
    review_reason: str | None = None
    operations: list[AfterSaleOperation] = Field(default_factory=list)


class CreateAfterSaleDraft(BaseModel):
    """买家确认前创建草稿所需的信息。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyer_id: BuyerId
    order_id: OrderId
    request_type: AfterSaleType
    reason: ReasonText


class UpdateAfterSaleDraft(BaseModel):
    """修改草稿时提交的新内容和当前版本。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    buyer_id: BuyerId
    request_type: AfterSaleType
    reason: ReasonText
    expected_version: int = Field(ge=1)


class SubmitAfterSaleRequest(BaseModel):
    """买家明确确认草稿时提交的数据。"""

    model_config = ConfigDict(extra="forbid")

    buyer_id: BuyerId
    expected_version: int = Field(ge=1)


class CancelAfterSaleDraft(BaseModel):
    """买家取消尚未提交的草稿。"""

    model_config = ConfigDict(extra="forbid")

    buyer_id: BuyerId
    expected_version: int = Field(ge=1)


class ReviewAfterSaleRequest(BaseModel):
    """客服审核待处理申请时提交的数据。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reviewer: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    decision: Literal["approved", "rejected"]
    reason: Annotated[str, StringConstraints(min_length=2, max_length=500)]
    expected_version: int = Field(ge=1)
