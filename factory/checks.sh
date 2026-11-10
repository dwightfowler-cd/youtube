#!/usr/bin/env bash
# The repo's checks. Exit 0 only when the branch is fit to review.
#
# The gate runs this script from the main checkout with the job's worktree as
# the working directory. Keep it fast, deterministic and non-interactive: no
# watch modes, no prompts, nothing that rewrites files.
#
# Excluded on purpose: tests marked `expensive` (spend API quota) and `oauth`
# (need a real token); pyproject's addopts already deselects `expensive`, and
# `oauth` tests skip without a token. No linter or typechecker is configured.
set -euo pipefail

uv sync --frozen
uv run --frozen pytest
uv run --frozen python -m compileall -q src
uv build --out-dir "$(mktemp -d)"
