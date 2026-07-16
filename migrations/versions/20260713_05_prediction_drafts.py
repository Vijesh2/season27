"""Add private prediction drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_05"
down_revision: str | None = "20260713_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("predicted_position", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "predicted_position >= 1 AND predicted_position <= 20",
            name="ck_predictions_position",
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "season_id", "predicted_position"),
        sa.UniqueConstraint("player_id", "season_id", "team_id"),
    )


def downgrade() -> None:
    op.drop_table("predictions")
