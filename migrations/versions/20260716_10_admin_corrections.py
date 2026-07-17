"""Add swap correction metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_10"
down_revision: str | None = "20260716_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("swaps") as batch_op:
        batch_op.add_column(sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("correction_reason", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("corrected_by_player_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_swaps_corrected_by_player_id_players",
            "players",
            ["corrected_by_player_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("swaps") as batch_op:
        batch_op.drop_constraint(
            "fk_swaps_corrected_by_player_id_players", type_="foreignkey"
        )
        batch_op.drop_column("corrected_by_player_id")
        batch_op.drop_column("correction_reason")
        batch_op.drop_column("corrected_at")
