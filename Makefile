.PHONY: api web test lint compose

api:
	cd backend && uv run uvicorn openlab.main:app --reload

web:
	cd web && pnpm dev

test:
	cd backend && uv run pytest
	cd web && pnpm test

lint:
	cd backend && uv run ruff check . && uv run mypy src
	cd web && pnpm lint && pnpm typecheck

compose:
	docker compose --env-file .env -f deploy/compose.yml up --build
