"""供模型调用且强制使用当前买家身份的订单工具。"""

from functools import lru_cache
from typing import Any, Literal

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.conversations.schemas import BuyerId
from app.modules.orders.repository import SqlOrderRepository
from app.modules.orders.schemas import OrderId
from app.modules.orders.service import OrderNotAccessibleError, OrderService


class ListOrdersInput(BaseModel):
    """列举当前买家订单不需要模型提供额外参数。"""

    model_config = ConfigDict(extra="forbid")


class OrderIdInput(BaseModel):
    """查询订单详情或物流时由模型提供的订单编号。"""

    model_config = ConfigDict(extra="forbid")

    order_id: OrderId = Field(description="五位订单编号，例如 10001")


class OrderToolError(BaseModel):
    """可由模型安全解释的订单工具错误。"""

    code: Literal["ORDER_NOT_ACCESSIBLE", "BUYER_CONTEXT_REQUIRED"]
    message: str


@lru_cache(maxsize=1)
def get_order_service() -> OrderService:
    """延迟创建数据库订单服务。"""

    initialize_database()
    repository = SqlOrderRepository(get_session_factory())
    return OrderService(repository)


def _buyer_from_config(config: RunnableConfig) -> BuyerId | None:
    """从服务端运行配置读取买家身份，不接受模型自行提供身份。"""

    configurable = config.get("configurable", {})
    buyer_id = configurable.get("buyer_id")
    if buyer_id in {"A", "B"}:
        return buyer_id
    return None


def _missing_buyer_result() -> dict[str, Any]:
    """返回缺少可信买家上下文时的稳定错误。"""

    error = OrderToolError(
        code="BUYER_CONTEXT_REQUIRED",
        message="当前会话缺少可验证的买家身份",
    )
    return {"success": False, "error": error.model_dump(mode="json")}


def _not_accessible_result(error: OrderNotAccessibleError) -> dict[str, Any]:
    """统一订单不存在和无权访问的模型可见结果。"""

    tool_error = OrderToolError(
        code="ORDER_NOT_ACCESSIBLE",
        message=str(error),
    )
    return {"success": False, "error": tool_error.model_dump(mode="json")}


@tool("list_orders", args_schema=ListOrdersInput)
def list_orders(config: RunnableConfig) -> dict[str, Any]:
    """列出当前会话买家自己的订单，用于订单不明确时提供候选项。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _missing_buyer_result()

    result = get_order_service().list_orders(buyer_id)
    return {
        "success": True,
        "orders": [order.model_dump(mode="json") for order in result.items],
        "total": result.total,
    }


@tool("get_order", args_schema=OrderIdInput)
def get_order(order_id: str, config: RunnableConfig) -> dict[str, Any]:
    """查询当前会话买家自己的订单详情。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _missing_buyer_result()

    try:
        order = get_order_service().get_order(order_id, buyer_id)
    except OrderNotAccessibleError as error:
        return _not_accessible_result(error)
    return {"success": True, "order": order.model_dump(mode="json")}


@tool("get_logistics", args_schema=OrderIdInput)
def get_logistics(order_id: str, config: RunnableConfig) -> dict[str, Any]:
    """查询当前会话买家自己的承运信息和物流轨迹。"""

    buyer_id = _buyer_from_config(config)
    if buyer_id is None:
        return _missing_buyer_result()

    try:
        logistics = get_order_service().get_logistics(order_id, buyer_id)
    except OrderNotAccessibleError as error:
        return _not_accessible_result(error)
    return {"success": True, "logistics": logistics.model_dump(mode="json")}


ORDER_TOOLS: tuple[BaseTool, ...] = (list_orders, get_order, get_logistics)


def get_order_tools() -> list[BaseTool]:
    """返回可以安全绑定给模型的订单工具。"""

    return list(ORDER_TOOLS)
