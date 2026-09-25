"""售后申请和操作记录的 SQLAlchemy 模型。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AfterSaleRecord(Base):
    """数据库中的一张售后申请。"""

    __tablename__ = "after_sales_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    buyer_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id"),
        nullable=False,
        index=True,
    )
    request_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(String(100))
    review_reason: Mapped[str | None] = mapped_column(String(500))
    operations: Mapped[list["AfterSaleOperationRecord"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="AfterSaleOperationRecord.created_at",
    )

    __table_args__ = (
        Index("ix_after_sales_buyer_updated", "buyer_id", "updated_at"),
    )


class AfterSaleOperationRecord(Base):
    """售后申请的一次状态或内容变更。"""

    __tablename__ = "after_sales_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("after_sales_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request: Mapped[AfterSaleRecord] = relationship(back_populates="operations")
