#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

if ! command -v just >/dev/null 2>&1; then
  echo "error: 'just' is required to run checks. Install: https://just.systems" >&2
  exit 1
fi

just check
