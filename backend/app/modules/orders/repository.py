"""订单与物流的 SQLAlchemy 数据访问层。"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.orders.models import OrderRecord
from app.modules.orders.schemas import (
    LogisticsEvent,
    Order,
    OrderItem,
)


class SqlOrderRepository:
    """只返回指定买家有权查看的订单。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_by_buyer(self, buyer_id: str) -> list[Order]:
        """返回买家的全部订单，最近下单的排在前面。"""

        with self._session_factory() as session:
            statement = (
                select(OrderRecord)
                .options(
                    selectinload(OrderRecord.items),
                    selectinload(OrderRecord.logistics_events),
                )
                .where(OrderRecord.buyer_id == buyer_id)
                .order_by(OrderRecord.ordered_at.desc(), OrderRecord.id.desc())
            )
            records = session.scalars(statement).all()
            orders: list[Order] = []
            for record in records:
                orders.append(self._to_schema(record))
            return orders

    def get_for_buyer(self, order_id: str, buyer_id: str) -> Order | None:
        """同时按订单号和买家查询，避免暴露其他买家的订单。"""

        with self._session_factory() as session:
            statement = (
                select(OrderRecord)
                .options(
                    selectinload(OrderRecord.items),
                    selectinload(OrderRecord.logistics_events),
                )
                .where(
                    OrderRecord.id == order_id,
                    OrderRecord.buyer_id == buyer_id,
                )
            )
            record = session.scalar(statement)
            if record is None:
                return None
            return self._to_schema(record)

    @classmethod
    def _to_schema(cls, record: OrderRecord) -> Order:
        """把订单及关联记录转换成稳定的接口格式。"""

        items: list[OrderItem] = []
        total_amount = Decimal("0.00")
        for item in record.items:
            order_item = OrderItem(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            items.append(order_item)
            total_amount += item.unit_price * item.quantity

        logistics: list[LogisticsEvent] = []
        for event in record.logistics_events:
            logistics.append(
                LogisticsEvent(
                    occurred_at=cls._with_timezone(event.occurred_at),
                    title=event.title,
                    detail=event.detail,
                )
            )

        return Order(
            id=record.id,
            buyer_id=record.buyer_id,
            status=record.status,
            ordered_at=record.ordered_at,
            carrier=record.carrier,
            tracking_number=record.tracking_number,
            items=items,
            logistics=logistics,
            total_amount=total_amount,
        )

    @staticmethod
    def _with_timezone(value: datetime) -> datetime:
        """SQLite 丢失时区标记时补回约定的 UTC。"""

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
