"""把知识向量从关系数据库迁移到 Milvus。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_07"
down_revision: str | None = "20260921_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """SQLite 只保留知识正文和索引元数据，向量改由 Milvus 保存。"""

    op.execute(
        sa.text(
            "UPDATE knowledge_chunks "
            "SET created_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_chunks "
            "SET updated_at = CURRENT_TIMESTAMP "
            "WHERE updated_at IS NULL"
        )
    )
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.drop_column("embedding_json")
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    """回滚时恢复旧向量列；已有 Milvus 向量不会自动复制回来。"""

    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.add_column(
            sa.Column("embedding_json", sa.Text(), nullable=False, server_default="[]")
        )
