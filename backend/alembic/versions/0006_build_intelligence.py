"""Add accepted BUILD design state, sourced pins, and expiring job results.

Revision ID: 0006_build_intelligence
Revises: 0005_profile_vectors
"""

from alembic import op

revision = "0006_build_intelligence"
down_revision = "0005_profile_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS result JSONB")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_expires_at ON jobs(expires_at)")

    op.execute(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS design_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )

    op.execute(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'user'"
    )
    op.execute("ALTER TABLE requirements ADD COLUMN IF NOT EXISTS role_key VARCHAR(120)")
    op.execute(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS selected_thing_id VARCHAR(36) REFERENCES things(id)"
    )
    op.execute("ALTER TABLE requirements ADD COLUMN IF NOT EXISTS match_status VARCHAR(30)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_requirements_selected_thing_id ON requirements(selected_thing_id)"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_requirement_source_role'
          ) THEN
            ALTER TABLE requirements ADD CONSTRAINT uq_requirement_source_role
              UNIQUE (project_id, source, role_key);
          END IF;
        END $$;
        """
    )

    op.execute("ALTER TABLE pins ADD COLUMN IF NOT EXISTS number VARCHAR(40)")
    op.execute(
        "ALTER TABLE pins ADD COLUMN IF NOT EXISTS electrical_type VARCHAR(40) NOT NULL DEFAULT 'passive'"
    )
    op.execute(
        "ALTER TABLE pins ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE pins ADD COLUMN IF NOT EXISTS source_ref VARCHAR(600)")
    op.execute(
        "ALTER TABLE pins ADD COLUMN IF NOT EXISTS verification_state VARCHAR(30) NOT NULL DEFAULT 'unverified'"
    )


def downgrade() -> None:
    op.drop_column("pins", "verification_state")
    op.drop_column("pins", "source_ref")
    op.drop_column("pins", "details")
    op.drop_column("pins", "electrical_type")
    op.drop_column("pins", "number")

    op.drop_constraint("uq_requirement_source_role", "requirements", type_="unique")
    op.drop_index("ix_requirements_selected_thing_id", table_name="requirements")
    op.drop_column("requirements", "match_status")
    op.drop_column("requirements", "selected_thing_id")
    op.drop_column("requirements", "role_key")
    op.drop_column("requirements", "source")

    op.drop_column("projects", "design_json")

    op.drop_index("ix_jobs_expires_at", table_name="jobs")
    op.drop_column("jobs", "expires_at")
    op.drop_column("jobs", "completed_at")
    op.drop_column("jobs", "result")
