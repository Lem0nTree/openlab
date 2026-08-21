"""Add Smart Inbox provider settings and processing evidence.

Revision ID: 0003_smart_inbox_provider
Revises: 0002_allocation_idempotency
"""

from alembic import op


revision = "0003_smart_inbox_provider"
down_revision = "0002_allocation_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'inbox_items' AND column_name = 'processing_evidence'
          ) THEN
            ALTER TABLE inbox_items ADD COLUMN processing_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'provider_configs' AND column_name = 'base_url'
          ) THEN
            ALTER TABLE provider_configs ADD COLUMN base_url VARCHAR(600);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'provider_configs' AND column_name = 'model'
          ) THEN
            ALTER TABLE provider_configs ADD COLUMN model VARCHAR(300);
          END IF;
        END $$;
        """
    )
    op.execute("UPDATE provider_configs SET base_url = 'http://localhost:11434/v1' WHERE base_url IS NULL")
    op.execute("UPDATE provider_configs SET model = 'llama3.2' WHERE model IS NULL")
    op.alter_column("provider_configs", "base_url", nullable=False)
    op.alter_column("provider_configs", "model", nullable=False)


def downgrade() -> None:
    op.drop_column("provider_configs", "model")
    op.drop_column("provider_configs", "base_url")
    op.drop_column("inbox_items", "processing_evidence")
