"""Store goals scored for standings tie-breaking."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_11"
down_revision: str | None = "20260716_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("standings") as batch_op:
        batch_op.add_column(sa.Column("goals_scored", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("standings") as batch_op:
        batch_op.drop_column("goals_scored")
