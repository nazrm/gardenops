.PHONY: dev dev-frontend test lint typecheck build clean

dev:
	.venv/bin/python -m uvicorn gardenops.main:app --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev

test:
	uv run python -m pytest tests/ -q --tb=short

lint:
	uvx ruff check gardenops/ && uvx ruff format --check gardenops/

typecheck:
	cd frontend && npx tsc --noEmit

build:
	cd frontend && npm run build

clean:
	rm -f garden.db
	rm -rf frontend/dist
