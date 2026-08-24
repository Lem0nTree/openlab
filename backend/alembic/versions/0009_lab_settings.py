"""Add owner-managed lab integration settings.

Revision ID: 0009_lab_settings
Revises: 0008_repair_profile_vectors
"""

from alembic import op

revision = "0009_lab_settings"
down_revision = "0008_repair_profile_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS kicad_cli VARCHAR(500)")


def downgrade() -> None:
    op.drop_column("labs", "kicad_cli")
