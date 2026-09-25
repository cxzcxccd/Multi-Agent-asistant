"""创建售后申请和操作记录表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_03"
down_revision: str | None = "20260916_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建售后状态、幂等键、活动约束和操作记录。"""

    op.create_table(
        "after_sales_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("buyer_id", sa.String(length=20), nullable=False),
        sa.Column("order_id", sa.String(length=20), nullable=False),
        sa.Column("request_type", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active_key", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer", sa.String(length=100), nullable=True),
        sa.Column("review_reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_key"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_after_sales_requests_buyer_id",
        "after_sales_requests",
        ["buyer_id"],
    )
    op.create_index(
        "ix_after_sales_requests_order_id",
        "after_sales_requests",
        ["order_id"],
    )
    op.create_index(
        "ix_after_sales_requests_status",
        "after_sales_requests",
        ["status"],
    )
    op.create_index(
        "ix_after_sales_buyer_updated",
        "after_sales_requests",
        ["buyer_id", "updated_at"],
    )

    op.create_table(
        "after_sales_operations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["after_sales_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_after_sales_operations_request_id",
        "after_sales_operations",
        ["request_id"],
    )


def downgrade() -> None:
    """删除售后操作和申请表。"""

    op.drop_table("after_sales_operations")
    op.drop_table("after_sales_requests")
