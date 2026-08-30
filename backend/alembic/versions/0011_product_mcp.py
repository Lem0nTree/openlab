"""Add product MCP OAuth grants and confirmation receipts."""

from alembic import op


revision = "0011_product_mcp"
down_revision = "0010_installation_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE labs ADD COLUMN IF NOT EXISTS mcp_enabled BOOLEAN NOT NULL DEFAULT false")
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
        id VARCHAR(120) PRIMARY KEY, name VARCHAR(200) NOT NULL,
        redirect_uris JSONB NOT NULL DEFAULT '[]'::jsonb,
        grant_types JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1)""")
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_grants (
        id VARCHAR(36) PRIMARY KEY, lab_id VARCHAR(36) NOT NULL REFERENCES labs(id),
        user_id VARCHAR(36) NOT NULL REFERENCES users(id), client_id VARCHAR(120) NOT NULL REFERENCES mcp_oauth_clients(id),
        scopes JSONB NOT NULL DEFAULT '[]'::jsonb, access_token_hash VARCHAR(128) UNIQUE,
        access_expires_at TIMESTAMPTZ, refresh_token_hash VARCHAR(128) UNIQUE,
        refresh_expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_grants_lab_id ON mcp_grants(lab_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_grants_user_id ON mcp_grants(user_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_authorization_codes (
        id VARCHAR(36) PRIMARY KEY, grant_id VARCHAR(36) NOT NULL REFERENCES mcp_grants(id),
        code_hash VARCHAR(128) UNIQUE NOT NULL, code_challenge VARCHAR(256) NOT NULL,
        redirect_uri VARCHAR(2000) NOT NULL, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now() NOT NULL)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_authorization_codes_grant_id ON mcp_authorization_codes(grant_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_action_receipts (
        id VARCHAR(36) PRIMARY KEY, grant_id VARCHAR(36) NOT NULL REFERENCES mcp_grants(id),
        action VARCHAR(120) NOT NULL, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        payload_hash VARCHAR(128) NOT NULL, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ,
        result JSONB, created_at TIMESTAMPTZ DEFAULT now() NOT NULL)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_action_receipts_grant_id ON mcp_action_receipts(grant_id)")
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_idempotency_results (
        id VARCHAR(36) PRIMARY KEY, grant_id VARCHAR(36) NOT NULL REFERENCES mcp_grants(id),
        request_id VARCHAR(128) NOT NULL, action VARCHAR(120) NOT NULL,
        result JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
        CONSTRAINT uq_mcp_idempotency UNIQUE(grant_id, request_id))""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_idempotency_results_grant_id ON mcp_idempotency_results(grant_id)")


def downgrade() -> None:
    op.drop_table("mcp_idempotency_results")
    op.drop_table("mcp_action_receipts")
    op.drop_table("mcp_authorization_codes")
    op.drop_table("mcp_grants")
    op.drop_table("mcp_oauth_clients")
    op.drop_column("labs", "mcp_enabled")
