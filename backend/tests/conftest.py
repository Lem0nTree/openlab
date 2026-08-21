"""Test settings that never connect to a live database during import."""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://openlab:openlab@127.0.0.1:5432/openlab_test"
)
