"""Cache auditable scene projections alongside legacy excerpt summaries."""

import sqlalchemy as sa
from alembic import op

revision = "20261014_12"
down_revision = "20261012_11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversation_summaries", sa.Column("scene_signature", sa.Text(), nullable=True))
    op.add_column("conversation_summaries", sa.Column("scenes_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("conversation_summaries", "scenes_json")
    op.drop_column("conversation_summaries", "scene_signature")
