"""Add season team roster and approval state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_03"
down_revision: str | None = "20260712_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("seasons") as batch_op:
        batch_op.add_column(sa.Column("roster_source", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("roster_imported_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("roster_approved_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("short_name", sa.String(length=30), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("source_identity", sa.String(length=100), nullable=False),
        sa.Column("badge_reference", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("source_identity"),
    )
    op.create_table(
        "season_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "display_order"),
        sa.UniqueConstraint("season_id", "team_id"),
    )


def downgrade() -> None:
    op.drop_table("season_teams")
    op.drop_table("teams")
    with op.batch_alter_table("seasons") as batch_op:
        batch_op.drop_column("roster_approved_at")
        batch_op.drop_column("roster_imported_at")
        batch_op.drop_column("roster_source")
