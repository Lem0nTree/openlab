"""Normalize legacy Inbox confidence values.

Revision ID: 0007_normalize_inbox_confidence
Revises: 0006_build_intelligence
"""

from alembic import op

revision = "0007_normalize_inbox_confidence"
down_revision = "0006_build_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
          WHEN 'unresolved' THEN 'unresolved'
          ELSE 'unresolved'
        END
        """
    )


def downgrade() -> None:
    pass
