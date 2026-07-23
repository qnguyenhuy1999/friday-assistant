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

migration-check:
    uv run pytest tests/persistence/test_migrations.py

persistence-check:
    uv run pytest tests/persistence

lock-check:
    uv run python scripts/lock_check.py

pre-commit:
    uv run pre-commit run --all-files
    uv run pre-commit run --all-files --hook-stage pre-push

# Fast, non-mutating local gate. architecture-check, policy-check,
# domain-check, schema-check, migration-check, and persistence-check are
# subsets already exercised by `test` (tests/architecture, tests/policy,
# tests/domain, tests/application, tests/contracts, tests/persistence);
# they're re-run here individually so a contributor gets an explicit,
# fast-failing signal naming exactly which dimension broke, at negligible
# cost (each subset runs in well under a second).
check: format-check lint typecheck test architecture-check policy-check domain-check schema-check migration-check persistence-check

# Full CI-equivalent gate. test-cov and lock-check are not part of `check`
# because test-cov needs coverage instrumentation (slower, and duplicates
# `test`'s pass/fail signal) and lock-check performs real package-manager
# installs (mutates the local environment, not appropriate for a fast local
# loop). pre-commit's pre-push stage re-runs typecheck/test-cov/lock-check
# again as an end-to-end proof that the hook wiring itself works — this is
# intentional overlap, not a bug.
ci: check test-cov lock-check pre-commit
    git diff --exit-code
    test -z "$(git status --porcelain)"

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    rm -rf apps/web/dist packages/contracts/dist packages/sdk-ts/dist
    rm -rf .markdownlint-cli2-cache
