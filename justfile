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
    pnpm exec tsc -p apps/web/tsconfig.typecheck.json
    pnpm exec tsc -p packages/contracts/tsconfig.typecheck.json
    pnpm exec tsc -p packages/sdk-ts/tsconfig.typecheck.json

test:
    uv run pytest

test-cov:
    uv run pytest --cov=src/friday --cov=apps/api --cov=apps/worker --cov-report=term-missing

check: format-check lint typecheck test

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    rm -rf apps/web/dist packages/contracts/dist packages/sdk-ts/dist
