"""Add privacy-preserving installation telemetry state and delivery outbox."""

from alembic import op

revision = "0012_usage_telemetry"
down_revision = "0011_product_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS telemetry_state (
        id VARCHAR(36) PRIMARY KEY, installation_id VARCHAR(36) UNIQUE NOT NULL,
        credential_ciphertext TEXT NOT NULL, usage_enabled BOOLEAN NOT NULL DEFAULT true,
        disclosure_version VARCHAR(40) NOT NULL DEFAULT '2026-08-31', onboarding_seen_at TIMESTAMPTZ,
        last_reported_day TIMESTAMPTZ, last_queued_day TIMESTAMPTZ, registered_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE IF NOT EXISTS communication_consents (
        id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) UNIQUE NOT NULL REFERENCES users(id),
        newsletter_opt_in BOOLEAN NOT NULL DEFAULT false, notice_version VARCHAR(40) NOT NULL,
        consented_at TIMESTAMPTZ, subscription_token_ciphertext TEXT, status VARCHAR(30) NOT NULL DEFAULT 'not_subscribed',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_communication_consents_user_id ON communication_consents(user_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS telemetry_outbox (
        id VARCHAR(36) PRIMARY KEY, kind VARCHAR(40) NOT NULL, idempotency_key VARCHAR(160) UNIQUE NOT NULL,
        activity_day TIMESTAMPTZ, consent_id VARCHAR(36) REFERENCES communication_consents(id),
        status VARCHAR(30) NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TIMESTAMPTZ NOT NULL, last_error TEXT, completed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_telemetry_outbox_kind ON telemetry_outbox(kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_telemetry_outbox_status ON telemetry_outbox(status)")


def downgrade() -> None:
    op.drop_table("telemetry_outbox")
    op.drop_table("communication_consents")
    op.drop_table("telemetry_state")
