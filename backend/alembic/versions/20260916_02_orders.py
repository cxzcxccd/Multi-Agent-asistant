"""创建订单、订单明细和物流节点表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_02"
down_revision: str | None = "20260916_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建订单查询所需的数据表和索引。"""

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("buyer_id", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("ordered_at", sa.Date(), nullable=False),
        sa.Column("carrier", sa.String(length=100), nullable=True),
        sa.Column("tracking_number", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_buyer_id", "orders", ["buyer_id"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index(
        "ix_orders_buyer_ordered",
        "orders",
        ["buyer_id", "ordered_at"],
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=20), nullable=False),
        sa.Column("product_id", sa.String(length=16), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])

    op.create_table(
        "logistics_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_logistics_events_order_id", "logistics_events", ["order_id"])
    op.create_index(
        "ux_logistics_order_position",
        "logistics_events",
        ["order_id", "position"],
        unique=True,
    )


def downgrade() -> None:
    """按外键依赖的相反顺序删除订单相关表。"""

    op.drop_table("logistics_events")
    op.drop_table("order_items")
    op.drop_table("orders")
