"""供模型查询售后进度和准备售后确认草稿的工具。"""

from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.after_sales.repository import SqlAfterSaleRepository
from app.modules.after_sales.schemas import AfterSaleStatus, AfterSaleType, ReasonText
from app.modules.after_sales.service import AfterSaleNotFoundError, AfterSaleService
from app.modules.conversations.schemas import BuyerId
from app.modules.orders.repository import SqlOrderRepository
from app.modules.orders.schemas import OrderId, OrderStatus
from app.modules.orders.service import OrderNotAccessibleError, OrderService


class EmptyInput(BaseModel):
    """不需要模型补充参数的工具输入。"""

    model_config = ConfigDict(extra="forbid")


class AfterSaleIdInput(BaseModel):
    """查询一笔售后申请所需的编号。"""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(description="售后申请 UUID")


class PrepareAfterSaleDraftInput(BaseModel):
    """模型整理售后草稿时使用的结构化参数。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    order_id: OrderId = Field(description="五位订单编号，例如 10002")
    request_type: AfterSaleType = Field(description="申请类型，只能是退货或换货")
    reason: ReasonText = Field(description="用户明确描述的商品问题或申请原因")


@lru_cache(maxsize=1)
def get_after_sale_tool_service() -> AfterSaleService:
    """延迟创建共享数据库上的售后业务服务。"""

    initialize_database()
    session_factory = get_session_factory()
    repository = SqlAfterSaleRepository(session_factory)
    order_service = OrderService(SqlOrderRepository(session_factory))
    return AfterSaleService(repository, order_service)


def _buyer_from_config(config: RunnableConfig) -> BuyerId | None:
    """只从服务端运行配置中读取买家身份。"""

    configurable = config.get("configurable", {})
    buyer_id = configurable.get("buyer_id")
    if buyer_id in {"A", "B"}:
        return buyer_id
    return None


def _buyer_required() -> dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": "BUYER_CONTEXT_REQUIRED",
            "message": "当前会话缺少可验证的买家身份",
        },
    }


@tool("list_after_sales", args_schema=EmptyInput)
def list_after_sales(config: RunnableConfig) -> dict[str, Any]:
    """列出当前买家的售后申请，用于查询申请进度。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _buyer_required()
    requests = get_after_sale_tool_service().list_for_buyer(buyer_id)
    return {
        "success": True,
        "requests": [request.model_dump(mode="json") for request in requests],
        "total": len(requests),
    }


@tool("get_after_sale", args_schema=AfterSaleIdInput)
def get_after_sale(request_id: UUID, config: RunnableConfig) -> dict[str, Any]:
    """查询当前买家一笔售后申请的完整状态。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _buyer_required()
    try:
        request = get_after_sale_tool_service().get_for_buyer(request_id, buyer_id)
    except AfterSaleNotFoundError:
        return {
            "success": False,
            "error": {
                "code": "AFTER_SALE_NOT_ACCESSIBLE",
                "message": "未找到可查询的售后申请",
            },
        }
    return {"success": True, "request": request.model_dump(mode="json")}


@tool("prepare_after_sale_draft", args_schema=PrepareAfterSaleDraftInput)
def prepare_after_sale_draft(
    order_id: str,
    request_type: AfterSaleType,
    reason: str,
    config: RunnableConfig,
) -> dict[str, Any]:
    """校验订单并返回需要买家确认的草稿；本工具不会写入数据库。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _buyer_required()

    service = get_after_sale_tool_service()
    try:
        order = service.order_service.get_order(order_id, buyer_id)
    except OrderNotAccessibleError:
        return {
            "success": False,
            "error": {
                "code": "ORDER_NOT_ACCESSIBLE",
                "message": "未找到可申请售后的订单",
            },
        }

    if order.status not in {OrderStatus.DELIVERED, OrderStatus.COMPLETED}:
        return {
            "success": False,
            "error": {
                "code": "ORDER_NOT_ELIGIBLE",
                "message": "订单当前状态暂不支持创建售后申请",
            },
        }

    active_requests = service.list_for_buyer(buyer_id)
    for request in active_requests:
        same_order = request.order_id == order_id
        active = request.status in {AfterSaleStatus.DRAFT, AfterSaleStatus.PENDING}
        if same_order and active:
            return {
                "success": False,
                "error": {
                    "code": "ACTIVE_AFTER_SALE_EXISTS",
                    "message": "该订单已经存在未完成的售后申请",
                },
            }

    return {
        "success": True,
        "artifact": {
            "type": "after_sale_draft",
            "requires_confirmation": True,
            "draft": {
                "order_id": order_id,
                "request_type": request_type.value,
                "reason": reason,
            },
        },
    }


AFTER_SALE_TOOLS: tuple[BaseTool, ...] = (
    list_after_sales,
    get_after_sale,
    prepare_after_sale_draft,
)


def get_after_sale_tools() -> list[BaseTool]:
    """返回可以安全绑定给模型的售后工具。"""

    return list(AFTER_SALE_TOOLS)
