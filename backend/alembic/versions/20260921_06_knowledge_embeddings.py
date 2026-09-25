"""为知识分段增加版本、哈希和向量字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_06"
down_revision: str | None = "20260921_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.add_column(sa.Column("document_version", sa.String(64), nullable=False, server_default="1"))
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("embedding_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("embedding_model", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_knowledge_chunks_content_hash", ["content_hash"])


def downgrade() -> None:
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.drop_index("ix_knowledge_chunks_content_hash")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("embedding_model")
        batch.drop_column("embedding_json")
        batch.drop_column("content_hash")
        batch.drop_column("document_version")
