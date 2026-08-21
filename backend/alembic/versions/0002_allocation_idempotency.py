"""Make project allocations idempotent.

Revision ID: 0002_allocation_idempotency
Revises: 0001_initial
"""

from alembic import op


revision = "0002_allocation_idempotency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 historically uses Base.metadata.create_all(), so a new installation
    # can already contain columns introduced by later models. Keep this upgrade
    # safe for both a genuine 0001 schema and that fresh-install shape.
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'allocations' AND column_name = 'idempotency_key'
          ) THEN
            ALTER TABLE allocations ADD COLUMN idempotency_key VARCHAR(128);
          END IF;
        END $$;
        """
    )
    op.execute("UPDATE allocations SET idempotency_key = id WHERE idempotency_key IS NULL")
    op.alter_column("allocations", "idempotency_key", nullable=False)
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_allocation_project_idempotency'
          ) THEN
            ALTER TABLE allocations ADD CONSTRAINT uq_allocation_project_idempotency
            UNIQUE (project_id, idempotency_key);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_allocation_project_idempotency", "allocations", type_="unique")
    op.drop_column("allocations", "idempotency_key")
