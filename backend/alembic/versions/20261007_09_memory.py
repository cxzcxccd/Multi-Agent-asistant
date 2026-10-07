"""增加买家长期偏好与会话摘录摘要。"""

import sqlalchemy as sa
from alembic import op

revision = "20261007_09"
down_revision = "20261001_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "buyer_memories",
        sa.Column("buyer_id", sa.String(20), primary_key=True),
        sa.Column("preferences", sa.Text(), nullable=False),
    )
    op.create_table(
        "conversation_summaries",
        sa.Column("conversation_id", sa.String(36), primary_key=True),
        sa.Column("source_signature", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_summaries")
    op.drop_table("buyer_memories")
