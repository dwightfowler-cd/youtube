#!/usr/bin/env bash
# The repo's checks. Exit 0 only when the branch is fit to review.
#
# The gate runs this script from the main checkout with the job's worktree as
# the working directory. Keep it fast, deterministic and non-interactive: no
# watch modes, no prompts, nothing that rewrites files.
#
# Every test runs, including the live `search` test (100 quota units per run).
# `oauth` tests need a real token and FAIL without one. Nothing may be skipped or
# deselected: any skipped, xfailed, xpassed or deselected test fails this script. Lint is ruff (rules in pyproject.toml); no
# typechecker is configured.
set -euo pipefail

uv sync --frozen
uv run --frozen ruff check .
pytest_log="$(mktemp)"
uv run --frozen pytest -rs 2>&1 | tee "$pytest_log"
if grep -Eq '[0-9]+ (skipped|xfailed|xpassed|deselected)' "$pytest_log"; then
  echo "FAIL: tests were skipped, deselected or marked xfail; a misconfigured or unrun test is a failure." >&2
  exit 1
fi
uv run --frozen python -m compileall -q src
uv build --out-dir "$(mktemp -d)"
