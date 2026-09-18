"""Private agents and scoped Google connections; preserve all historical data."""

from alembic import op
import sqlalchemy as sa

revision = "e06a90000001"
down_revision = "d05ca1100001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("tool_fixtures", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column(
        "workspaces",
        sa.Column(
            "tool_policy",
            sa.JSON(),
            nullable=False,
            server_default='["knowledge_search","read_source","web_search","web_read","gmail_search","gmail_read","drive_search","drive_read","gmail_save_draft"]',
        ),
    )
    op.add_column("workflows", sa.Column("owner_id", sa.String(36), nullable=True))
    op.create_index("ix_workflows_owner_id", "workflows", ["owner_id"])
    op.add_column("workflows", sa.Column("kind", sa.String(20), nullable=False, server_default="workflow"))
    op.add_column("workflows", sa.Column("starters", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("workflows", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("workflows", sa.Column("submitted_from", sa.String(36)))
    op.add_column("runs", sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Definitions are shared only for creation of new tables, never altering old payloads.
    from backend.models import GoogleConnection, OAuthState, ToolReceipt, GmailDraft

    for model in [GoogleConnection, OAuthState, ToolReceipt, GmailDraft]:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for name in ["gmail_drafts", "tool_receipts", "oauth_states", "google_connections"]:
        op.drop_table(name)
    op.drop_column("runs", "private")
    op.drop_column("runs", "tool_fixtures")
    op.drop_index("ix_workflows_owner_id", table_name="workflows")
    for name in ["submitted_from", "archived", "starters", "kind", "owner_id"]:
        op.drop_column("workflows", name)
    op.drop_column("workspaces", "tool_policy")
