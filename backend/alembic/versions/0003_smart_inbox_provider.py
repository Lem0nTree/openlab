"""Add Smart Inbox provider settings and processing evidence.

Revision ID: 0003_smart_inbox_provider
Revises: 0002_allocation_idempotency
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_smart_inbox_provider"
down_revision = "0002_allocation_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbox_items",
        sa.Column(
            "processing_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("provider_configs", sa.Column("base_url", sa.String(length=600), nullable=True))
    op.add_column("provider_configs", sa.Column("model", sa.String(length=300), nullable=True))
    op.execute("UPDATE provider_configs SET base_url = 'http://localhost:11434/v1' WHERE base_url IS NULL")
    op.execute("UPDATE provider_configs SET model = 'llama3.2' WHERE model IS NULL")
    op.alter_column("provider_configs", "base_url", nullable=False)
    op.alter_column("provider_configs", "model", nullable=False)


def downgrade() -> None:
    op.drop_column("provider_configs", "model")
    op.drop_column("provider_configs", "base_url")
    op.drop_column("inbox_items", "processing_evidence")
