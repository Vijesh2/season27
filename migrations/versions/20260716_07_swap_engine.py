"""Add the player swap engine."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_07"
down_revision: str | None = "20260713_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "swaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("swap_window_id", sa.Integer(), nullable=False),
        sa.Column("first_team_id", sa.Integer(), nullable=False),
        sa.Column("second_team_id", sa.Integer(), nullable=False),
        sa.Column("first_position", sa.Integer(), nullable=False),
        sa.Column("second_position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["swap_window_id"], ["swap_windows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["first_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["second_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "season_id", "swap_window_id"),
    )


def downgrade() -> None:
    op.drop_table("swaps")
