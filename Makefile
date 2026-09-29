.PHONY: help setup backend worker beat frontend test test-ml lint smoke up down logs clean hero

help:
	@echo "PixelForge"
	@echo "  make setup      install backend + frontend dependencies"
	@echo "  make backend    run the API on :8000"
	@echo "  make frontend   run the UI on :5173"
	@echo "  make worker     run a Celery worker (needs Redis)"
	@echo "  make test       run the full backend test suite"
	@echo "  make smoke      run one model end to end, no services required"
	@echo "  make up/down    docker compose"

setup:
	cd backend && python -m venv .venv && . .venv/bin/activate && \
		pip install -U pip && pip install -r requirements/dev.txt
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@echo "Ready. Run 'make backend' and 'make frontend' in two terminals."

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

worker:
	cd backend && celery -A app.tasks.celery_app worker -Q cpu,gpu --pool=solo -l info

beat:
	cd backend && celery -A app.tasks.celery_app beat -l info

test:
	cd backend && python -m pytest tests -q

test-ml:
	cd backend && python -m pytest tests/ml -q

lint:
	cd backend && ruff check app tests
	cd frontend && npm run typecheck

smoke:
	cd backend && python scripts/smoke_adapter.py --list

hero:
	python scripts/make_hero_assets.py --input $(INPUT) --out frontend/public/hero

up:
	docker compose -f infrastructure/compose/docker-compose.yml up -d --build

down:
	docker compose -f infrastructure/compose/docker-compose.yml down

logs:
	docker compose -f infrastructure/compose/docker-compose.yml logs -f api worker

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache frontend/dist data
