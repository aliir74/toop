.PHONY: install dev test lint format typecheck check run deploy logs ssh clean

install:
	uv sync

dev:
	uv sync --all-extras

test:
	uv run pytest -v

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run ty check src/

check: lint typecheck test

run:
	uv run python -m toop

deploy:
	./deploy.sh

logs:
	@VPS_SSH=$$(grep -E "^VPS_SSH=" .env | cut -d'=' -f2-); \
	ssh "$$VPS_SSH" "cd /opt/toop && docker compose logs -f --tail=100"

ssh:
	@VPS_SSH=$$(grep -E "^VPS_SSH=" .env | cut -d'=' -f2-); \
	ssh "$$VPS_SSH"

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
