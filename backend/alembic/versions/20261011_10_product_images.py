"""保存商品图片路径。"""

import sqlalchemy as sa
from alembic import op

revision = "20261011_10"
down_revision = "20261007_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("image_url", sa.String(300), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("products", "image_url")
