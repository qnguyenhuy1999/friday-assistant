#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

require_command() {
  local name="$1"
  local hint="$2"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "error: required command '${name}' not found." >&2
    echo "  ${hint}" >&2
    exit 1
  fi
}

require_command uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
require_command node "Install Node.js 22 (LTS): https://nodejs.org/"
require_command corepack "Corepack ships with Node.js >= 16.9; if missing, upgrade Node."
require_command just "Install just: https://just.systems"

echo "== Toolchain versions =="
echo "python:   $(python3 --version)"
echo "uv:       $(uv --version)"
echo "node:     $(node --version)"
echo "corepack: $(corepack --version)"

echo "== Syncing Python dependencies (uv) =="
uv sync

echo "== Preparing pnpm via Corepack =="
corepack enable >/dev/null 2>&1 || true
corepack prepare --activate

echo "== Installing Node dependencies (pnpm, frozen lockfile) =="
pnpm install --frozen-lockfile

echo "pnpm:     $(pnpm --version)"
echo "Bootstrap complete."
