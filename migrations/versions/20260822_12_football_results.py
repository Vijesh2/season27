"""Store imported football results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_12"
down_revision: str | None = "20260821_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "football_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.String(length=100), nullable=False),
        sa.Column("competition", sa.String(length=100), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_status", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id"),
    )
    op.create_index(
        op.f("ix_football_results_scheduled_at"),
        "football_results",
        ["scheduled_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_football_results_source_event_id"),
        "football_results",
        ["source_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_football_results_source_event_id"), table_name="football_results")
    op.drop_index(op.f("ix_football_results_scheduled_at"), table_name="football_results")
    op.drop_table("football_results")
