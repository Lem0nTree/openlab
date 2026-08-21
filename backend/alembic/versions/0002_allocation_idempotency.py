"""Make project allocations idempotent.

Revision ID: 0002_allocation_idempotency
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_allocation_idempotency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "allocations",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.execute("UPDATE allocations SET idempotency_key = id WHERE idempotency_key IS NULL")
    op.alter_column("allocations", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_allocation_project_idempotency",
        "allocations",
        ["project_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_allocation_project_idempotency", "allocations", type_="unique")
    op.drop_column("allocations", "idempotency_key")
