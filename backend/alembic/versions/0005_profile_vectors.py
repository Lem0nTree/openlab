"""Add embedding configuration and native pgvector profile storage.

Revision ID: 0005_profile_vectors
Revises: 0004_item_intelligence_inbox
"""

from alembic import op

revision = "0005_profile_vectors"
down_revision = "0004_item_intelligence_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE provider_configs ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(300)")
    op.execute(
        "ALTER TABLE provider_configs ADD COLUMN IF NOT EXISTS embeddings_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS purpose VARCHAR(40) NOT NULL DEFAULT 'profile'"
    )
    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS profile_text TEXT")
    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_vector vector")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'embeddings' AND column_name = 'vector_json'
          ) THEN
            UPDATE embeddings
            SET embedding_vector = vector_json::text::vector
            WHERE embedding_vector IS NULL;
            ALTER TABLE embeddings DROP COLUMN vector_json;
          END IF;
        END $$;
        """
    )
    op.execute("UPDATE embeddings SET profile_text = '' WHERE profile_text IS NULL")
    op.alter_column("embeddings", "profile_text", nullable=False)
    op.alter_column("embeddings", "embedding_vector", nullable=False)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embeddings_profile_thing ON embeddings(thing_id, purpose)"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_embedding_space_thing'
          ) THEN
            ALTER TABLE embeddings ADD CONSTRAINT uq_embedding_space_thing
              UNIQUE (thing_id, purpose, provider, model);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE embeddings ADD COLUMN vector_json JSONB")
    op.execute("UPDATE embeddings SET vector_json = embedding_vector::text::jsonb")
    op.alter_column("embeddings", "vector_json", nullable=False)
    op.drop_constraint("uq_embedding_space_thing", "embeddings", type_="unique")
    op.drop_index("ix_embeddings_profile_thing", table_name="embeddings")
    op.drop_column("embeddings", "embedding_vector")
    op.drop_column("embeddings", "profile_text")
    op.drop_column("embeddings", "purpose")
    op.drop_column("provider_configs", "embeddings_enabled")
    op.drop_column("provider_configs", "embedding_model")
