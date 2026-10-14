"""保存真实模型用量及 MCP 执行统计。"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_11"
down_revision = "20261011_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("telemetry_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "telemetry_json")
