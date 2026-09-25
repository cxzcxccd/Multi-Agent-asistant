"""订单归属与物流查询业务规则。"""

from typing import Protocol

from app.modules.conversations.schemas import BuyerId
from app.modules.orders.schemas import LogisticsResponse, Order, OrderListResponse


class OrderReader(Protocol):
    """订单服务依赖的最小仓库接口。"""

    def list_by_buyer(self, buyer_id: str) -> list[Order]: ...

    def get_for_buyer(self, order_id: str, buyer_id: str) -> Order | None: ...


class OrderNotAccessibleError(LookupError):
    """订单不存在或不属于当前买家。"""

    def __init__(self) -> None:
        super().__init__("未找到可查询的订单，请核对或联系人工")


class OrderService:
    """提供不会泄露其他买家数据的订单查询。"""

    def __init__(self, repository: OrderReader) -> None:
        self.repository = repository

    def list_orders(self, buyer_id: BuyerId) -> OrderListResponse:
        """列出当前买家的订单。"""

        orders = self.repository.list_by_buyer(buyer_id)
        return OrderListResponse(items=orders, total=len(orders))

    def get_order(self, order_id: str, buyer_id: BuyerId) -> Order:
        """读取属于当前买家的单笔订单。"""

        order = self.repository.get_for_buyer(order_id, buyer_id)
        if order is None:
            raise OrderNotAccessibleError()
        return order

    def get_logistics(self, order_id: str, buyer_id: BuyerId) -> LogisticsResponse:
        """读取订单当前状态、承运信息和物流时间线。"""

        order = self.get_order(order_id, buyer_id)
        return LogisticsResponse(
            order_id=order.id,
            status=order.status,
            carrier=order.carrier,
            tracking_number=order.tracking_number,
            events=order.logistics,
        )
