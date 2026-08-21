# OpenLab server

Run API migrations before serving production traffic:

```bash
uv run alembic upgrade head
uv run uvicorn openlab.main:app --host 0.0.0.0 --port 8000
```

`DATABASE_URL` must point to PostgreSQL. SQLite is deliberately unsupported.

