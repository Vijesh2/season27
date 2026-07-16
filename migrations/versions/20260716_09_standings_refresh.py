"""Add standings refresh metadata and throttles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_09"
down_revision: str | None = "20260716_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("standings_snapshots") as batch_op:
        batch_op.add_column(sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE standings_snapshots SET refreshed_at = recorded_at")
    with op.batch_alter_table("standings_snapshots") as batch_op:
        batch_op.alter_column("refreshed_at", nullable=False)
    op.create_table(
        "standings_refresh_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("incident_open", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id"),
    )
    op.create_table(
        "standings_refresh_throttles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_standings_refresh_throttles_key_hash"),
        "standings_refresh_throttles",
        ["key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_standings_refresh_throttles_key_hash"),
        table_name="standings_refresh_throttles",
    )
    op.drop_table("standings_refresh_throttles")
    op.drop_table("standings_refresh_states")
    with op.batch_alter_table("standings_snapshots") as batch_op:
        batch_op.drop_column("refreshed_at")
