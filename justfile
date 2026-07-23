set shell := ["bash", "-euo", "pipefail", "-c"]

bootstrap:
    ./scripts/bootstrap.sh

format:
    uv run ruff format .
    pnpm exec prettier --write "**/*.{json,yaml,yml}" "apps/**/*.ts" "packages/**/*.ts" eslint.config.mjs

format-check:
    uv run ruff format --check .
    pnpm exec prettier --check "**/*.{json,yaml,yml}" "apps/**/*.ts" "packages/**/*.ts" eslint.config.mjs

lint:
    uv run ruff check .
    pnpm exec eslint .

typecheck:
    uv run mypy
    pnpm exec tsc --build tsconfig.json

test:
    uv run pytest

test-cov:
    uv run pytest --cov=src --cov=apps --cov=tests --cov-report=term-missing

check: format-check lint typecheck test

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    rm -rf apps/web/dist packages/contracts/dist packages/sdk-ts/dist
