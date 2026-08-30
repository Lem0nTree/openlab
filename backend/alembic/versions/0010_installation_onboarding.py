"""Persist onboarding state and worker liveness, preserving existing installations."""

from alembic import op

revision = "0010_installation_onboarding"
down_revision = "0009_lab_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 creates current Base.metadata on a fresh installation.
    op.execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS public_url VARCHAR(600)")
    op.execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS public_url_verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE labs ADD COLUMN IF NOT EXISTS integration_checks JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("""CREATE TABLE IF NOT EXISTS service_heartbeats (
        instance_id VARCHAR(36) PRIMARY KEY,
        service VARCHAR(40) NOT NULL,
        version VARCHAR(100) NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL
    )""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_service_heartbeats_service ON service_heartbeats(service)"
    )


def downgrade() -> None:
    op.drop_table("service_heartbeats")
    op.drop_column("labs", "integration_checks")
    op.drop_column("labs", "onboarding_completed_at")
    op.drop_column("labs", "public_url_verified_at")
    op.drop_column("labs", "public_url")
