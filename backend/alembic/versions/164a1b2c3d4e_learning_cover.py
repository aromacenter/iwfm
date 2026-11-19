"""X-Learning: rajzolt borító (SVG) + pasztell alapszín a tananyagon (164. kör)

Revision ID: 164a1b2c3d4e
Revises: 161a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "164a1b2c3d4e"
down_revision = "161a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("learning_materials")
    with op.batch_alter_table("learning_materials") as batch:
        if "cover_svg" not in have:
            batch.add_column(sa.Column("cover_svg", sa.Text(), nullable=True))
        if "cover_color" not in have:
            batch.add_column(sa.Column("cover_color", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("learning_materials") as batch:
        batch.drop_column("cover_color")
        batch.drop_column("cover_svg")
