"""创建商品、会话和消息表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建第一版业务数据表和查询索引。"""

    op.create_table(
        "products",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("series", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("specs", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("color", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=300), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_price", "products", ["price"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("buyer_id", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_buyer_id", "conversations", ["buyer_id"])
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])
    op.create_index(
        "ix_conversations_buyer_updated",
        "conversations",
        ["buyer_id", "updated_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index(
        "ux_messages_conversation_position",
        "messages",
        ["conversation_id", "position"],
        unique=True,
    )


def downgrade() -> None:
    """按外键依赖的相反顺序删除第一版数据表。"""

    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("products")
