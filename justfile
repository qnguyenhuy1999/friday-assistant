set shell := ["bash", "-euo", "pipefail", "-c"]

bootstrap:
    ./scripts/bootstrap.sh

format:
    uv run ruff format .
    pnpm exec prettier --write "*.json" "*.yaml" eslint.config.mjs

format-check:
    uv run ruff format --check .
    pnpm exec prettier --check "*.json" "*.yaml" eslint.config.mjs

lint:
    uv run ruff check .
    pnpm exec eslint eslint.config.mjs

typecheck:
    uv run mypy

test:
    uv run pytest

test-cov:
    uv run pytest --cov=tests --cov-report=term-missing

check: format-check lint typecheck test

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
