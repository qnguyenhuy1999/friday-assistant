set shell := ["bash", "-euo", "pipefail", "-c"]

bootstrap:
    ./scripts/bootstrap.sh

format:
    uv run ruff format .
    pnpm exec prettier --write "**/*.{json,yaml,yml}" "apps/**/*.{ts,tsx}" "packages/**/*.{ts,tsx}" eslint.config.mjs

format-check:
    uv run ruff format --check .
    pnpm exec prettier --check "**/*.{json,yaml,yml}" "apps/**/*.{ts,tsx}" "packages/**/*.{ts,tsx}" eslint.config.mjs

lint:
    uv run ruff check .
    pnpm exec eslint .
    pnpm exec markdownlint-cli2 "**/*.md"

shellcheck:
    uv run shellcheck scripts/bootstrap.sh scripts/check.sh

typecheck:
    uv run mypy
    pnpm exec tsc -p apps/web/tsconfig.typecheck.json
    pnpm exec tsc -p packages/contracts/tsconfig.typecheck.json
    pnpm exec tsc -p packages/sdk-ts/tsconfig.typecheck.json

test:
    uv run pytest

test-ts:
    pnpm --filter @friday/sdk test
    pnpm --filter @friday/web test

e2e:
    pnpm --filter @friday/web test:e2e

test-cov:
    uv run pytest --cov=src/friday --cov=apps/api --cov=apps/worker --cov-report=term-missing

architecture-check:
    uv run pytest tests/architecture

policy-check:
    uv run pytest tests/policy

domain-check:
    uv run pytest tests/domain tests/application tests/architecture

schema-check:
    uv run pytest tests/contracts

schema-parity-check:
    uv run pytest tests/persistence/test_schema_parity.py

migration-check:
    uv run pytest tests/persistence/test_migrations.py

persistence-check:
    uv run pytest tests/persistence

lock-check:
    uv run python scripts/lock_check.py

generate-contracts:
    pnpm generate:contracts

contracts-check:
    pnpm check:contracts

dependency-audit:
    uv run pip-audit --local
    pnpm audit:dependencies

worker:
    uv run python -m apps.worker.main

worker-check:
    uv run python -m apps.worker.preflight

# Full non-mutating local dependency diagnosis. The worker preflight already
# owns these checks; keep one source of truth rather than duplicating probes.
doctor: worker-check

memory-check:
    uv run python -m apps.worker.preflight --memory-only

memory-index:
    uv run python -m apps.worker.preflight --memory-index

pre-commit:
    uv run pre-commit run --all-files
    uv run pre-commit run --all-files --hook-stage pre-push

# Fast, non-mutating local gate. The full Python test run already includes
# architecture, policy, domain, contract, migration, and persistence tests;
# their named recipes remain available for focused diagnosis without making
# every normal check run those same tests twice.
check: contracts-check format-check lint shellcheck typecheck test test-ts

# Full CI-equivalent gate. test-cov and lock-check are not part of `check`
# because test-cov needs coverage instrumentation (slower, and duplicates
# `test`'s pass/fail signal) and lock-check performs real package-manager
# installs (mutates the local environment, not appropriate for a fast local
# loop). Dependency audits need network access, so they run only in the full
# release-equivalent gate.
ci: check test-cov e2e lock-check dependency-audit
    git diff --exit-code
    test -z "$(git status --porcelain)"

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    rm -rf apps/web/dist packages/contracts/dist packages/sdk-ts/dist
    rm -rf .markdownlint-cli2-cache
