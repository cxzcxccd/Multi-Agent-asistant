"""创建知识分段表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_05"
down_revision: str | None = "20260916_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_key", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_key", "position"),
    )
    op.create_index("ix_knowledge_chunks_category", "knowledge_chunks", ["category"])
    op.create_index("ix_knowledge_chunks_document_key", "knowledge_chunks", ["document_key"])


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
