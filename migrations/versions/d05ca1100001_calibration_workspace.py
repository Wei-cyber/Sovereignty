"""Persist workspace grader checks and human review."""

from alembic import op
import sqlalchemy as sa

revision = "d05ca1100001"
down_revision = "c04b384e0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "grader_calibrations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *[
            sa.Column(name, sa.JSON(), nullable=False)
            for name in ("examples", "model_profile", "dataset", "report", "results")
        ],
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_grader_calibrations_workspace_id", "grader_calibrations", ["workspace_id"])


def downgrade():
    op.drop_table("grader_calibrations")
