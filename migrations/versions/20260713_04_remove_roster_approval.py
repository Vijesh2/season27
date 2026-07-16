"""Remove unnecessary roster approval state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_04"
down_revision: str | None = "20260713_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("seasons") as batch_op:
        batch_op.drop_column("roster_approved_at")
        batch_op.drop_column("roster_imported_at")
        batch_op.drop_column("roster_source")


def downgrade() -> None:
    with op.batch_alter_table("seasons") as batch_op:
        batch_op.add_column(sa.Column("roster_source", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("roster_imported_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("roster_approved_at", sa.DateTime(timezone=True), nullable=True)
        )
