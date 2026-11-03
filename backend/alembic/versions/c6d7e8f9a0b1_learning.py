"""X-Learning: tananyagok, csatolmányok, kérdések, kiosztások, próbálkozások (142. kör)

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_materials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("video_url", sa.String(length=512), nullable=True),
        sa.Column("validity_months", sa.Integer(), nullable=True),
        sa.Column("random_count", sa.Integer(), nullable=True),
        sa.Column("external_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_roles", sa.JSON(), nullable=True),
        sa.Column("default_due_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "learning_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("learning_materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_learning_att_material", "learning_attachments", ["material_id"])
    op.create_table(
        "learning_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("learning_materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_learning_q_material", "learning_questions", ["material_id", "sort"])
    op.create_table(
        "learning_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("learning_materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Uuid(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("assigned_by_name", sa.String(length=256), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("reminded_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_id", "employee_id", name="uq_learning_assignment"),
    )
    op.create_index("ix_learning_asg_employee", "learning_assignments", ["employee_id"])
    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("learning_assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("score_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_learning_attempt_asg", "learning_attempts", ["assignment_id", "created_at"])


def downgrade() -> None:
    for name in ("learning_attempts", "learning_assignments", "learning_questions", "learning_attachments", "learning_materials"):
        op.drop_table(name)
