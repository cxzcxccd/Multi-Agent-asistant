"""订单、订单明细和物流节点的 SQLAlchemy 模型。"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OrderRecord(Base):
    """数据库中的一笔模拟订单。"""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    buyer_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    ordered_at: Mapped[date] = mapped_column(Date, nullable=False)
    carrier: Mapped[str | None] = mapped_column(String(100))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    items: Mapped[list["OrderItemRecord"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItemRecord.id",
    )
    logistics_events: Mapped[list["LogisticsEventRecord"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="LogisticsEventRecord.position",
    )

    __table_args__ = (
        Index("ix_orders_buyer_ordered", "buyer_id", "ordered_at"),
    )


class OrderItemRecord(Base):
    """订单中的一项商品及下单价格快照。"""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"),
        nullable=False,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    order: Mapped[OrderRecord] = relationship(back_populates="items")


class LogisticsEventRecord(Base):
    """一笔订单按时间排列的物流节点。"""

    __tablename__ = "logistics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    order: Mapped[OrderRecord] = relationship(back_populates="logistics_events")

    __table_args__ = (
        Index(
            "ux_logistics_order_position",
            "order_id",
            "position",
            unique=True,
        ),
    )
