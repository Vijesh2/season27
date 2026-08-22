"""Store bulletin drafts, publication state, facts, and source matches."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_13"
down_revision: str | None = "20260822_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bulletins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fact_pack", sa.JSON(), nullable=False),
        sa.Column("created_by_player_id", sa.Integer(), nullable=False),
        sa.Column("published_by_player_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'suppressed')",
            name="ck_bulletins_status",
        ),
        sa.ForeignKeyConstraint(["created_by_player_id"], ["players.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_player_id"], ["players.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "period_end"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_bulletins_slug"), "bulletins", ["slug"], unique=True)
    op.create_index(op.f("ix_bulletins_status"), "bulletins", ["status"], unique=False)
    op.create_table(
        "bulletin_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bulletin_id", sa.Integer(), nullable=False),
        sa.Column("football_result_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bulletin_id"], ["bulletins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["football_result_id"], ["football_results.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bulletin_id", "football_result_id"),
    )


def downgrade() -> None:
    op.drop_table("bulletin_matches")
    op.drop_index(op.f("ix_bulletins_status"), table_name="bulletins")
    op.drop_index(op.f("ix_bulletins_slug"), table_name="bulletins")
    op.drop_table("bulletins")
