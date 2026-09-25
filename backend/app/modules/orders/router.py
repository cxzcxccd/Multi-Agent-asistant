"""订单列表、详情和物流查询接口。"""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.db.initialize import initialize_database
from app.db.session import get_session_factory
from app.modules.auth.dependencies import BuyerPrincipal
from app.modules.orders.repository import SqlOrderRepository
from app.modules.orders.schemas import (
    LogisticsResponse,
    Order,
    OrderId,
    OrderListResponse,
)
from app.modules.orders.service import OrderNotAccessibleError, OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@lru_cache(maxsize=1)
def get_order_service() -> OrderService:
    """复用连接数据库的订单服务。"""

    initialize_database()
    repository = SqlOrderRepository(get_session_factory())
    return OrderService(repository)


OrderServiceDependency = Annotated[OrderService, Depends(get_order_service)]


@router.get("", response_model=OrderListResponse, summary="查询当前买家的订单")
async def list_orders(
    service: OrderServiceDependency,
    user: BuyerPrincipal,
) -> OrderListResponse:
    """只返回当前买家有权查看的订单。"""

    return service.list_orders(user.buyer_id)


@router.get(
    "/{order_id}",
    response_model=Order,
    responses={status.HTTP_404_NOT_FOUND: {"description": "订单不可查询"}},
    summary="查询订单详情",
)
async def get_order(
    order_id: Annotated[OrderId, Path(description="订单编号，例如 10001")],
    service: OrderServiceDependency,
    user: BuyerPrincipal,
) -> Order:
    """查询订单详情，不区分不存在和无权访问。"""

    try:
        return service.get_order(order_id, user.buyer_id)
    except OrderNotAccessibleError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get(
    "/{order_id}/logistics",
    response_model=LogisticsResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "订单不可查询"}},
    summary="查询订单物流",
)
async def get_logistics(
    order_id: Annotated[OrderId, Path(description="订单编号，例如 10001")],
    service: OrderServiceDependency,
    user: BuyerPrincipal,
) -> LogisticsResponse:
    """查询承运信息和物流轨迹，不承诺未返回的送达时间。"""

    try:
        return service.get_logistics(order_id, user.buyer_id)
    except OrderNotAccessibleError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
