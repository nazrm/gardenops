.PHONY: dev dev-frontend test lint typecheck build clean

dev:
	.venv/bin/python -m uvicorn gardenops.main:app --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	uv run python -m pytest tests/ -q --tb=short

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	cd frontend && npm run typecheck

build:
	cd frontend && npm run build

clean:
	rm -f garden.db
	rm -rf frontend/dist
