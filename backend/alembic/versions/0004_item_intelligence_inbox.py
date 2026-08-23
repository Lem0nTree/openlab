"""Add independent Inbox candidate lifecycle and temporary artifact metadata.

Revision ID: 0004_item_intelligence_inbox
Revises: 0003_smart_inbox_provider
"""

from alembic import op

revision = "0004_item_intelligence_inbox"
down_revision = "0003_smart_inbox_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE inbox_candidates ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'proposed'"
    )
    op.execute(
        "ALTER TABLE inbox_candidates ADD COLUMN IF NOT EXISTS thing_id VARCHAR(36) REFERENCES things(id)"
    )
    op.execute("ALTER TABLE inbox_candidates ADD COLUMN IF NOT EXISTS product_url VARCHAR(2000)")
    op.execute(
        """
        UPDATE inbox_candidates
        SET confidence = CASE confidence
            WHEN 'confirmed' THEN 'high'
            WHEN 'likely' THEN 'medium'
            WHEN 'generic' THEN 'low'
            WHEN 'high' THEN 'high'
            WHEN 'medium' THEN 'medium'
            WHEN 'low' THEN 'low'
            ELSE 'unresolved'
        END
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_inbox_candidates_status ON inbox_candidates(status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_inbox_candidates_thing_id ON inbox_candidates(thing_id)"
    )
    op.execute("ALTER TABLE attachments ALTER COLUMN storage_key DROP NOT NULL")
    op.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ")
    op.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS cleanup_error TEXT")


def downgrade() -> None:
    op.drop_column("attachments", "cleanup_error")
    op.drop_column("attachments", "purged_at")
    op.alter_column("attachments", "storage_key", nullable=False)
    op.drop_index("ix_inbox_candidates_thing_id", table_name="inbox_candidates")
    op.drop_index("ix_inbox_candidates_status", table_name="inbox_candidates")
    op.drop_column("inbox_candidates", "product_url")
    op.drop_column("inbox_candidates", "thing_id")
    op.drop_column("inbox_candidates", "status")
