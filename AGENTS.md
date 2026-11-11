## Software factory

`/factory run` builds every open GitHub issue labeled `factory` through
intake → spec → build → gate → review → approve, one PR per issue. State is in
`factory/jobs/` (gitignored); `factory/bin/job` is its only writer. Checks:
`factory/checks.sh`. Dashboard: `cd factory && python3 -m http.server`, then
open http://localhost:8000/dashboard.html.

Each job works in its own git worktree, a full checkout of the repo at
`.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't
read `.gitignore` sees those copies too; exclude `.worktrees/` in its config.

## Setup and validation

- Setup: `uv sync` (Python >=3.13). No env files; real OAuth is only needed for
  `-m oauth` tests, via `uv run youtube auth`.
- Run the server: `uv run youtube` (stdio MCP server, no dev server or port).
- Validate (same as `factory/checks.sh`): `uv run pytest`,
  `uv run python -m compileall -q src`, `uv build`.
- `-m expensive` tests spend YouTube API quota; don't run them unprompted.

## Testing rule: a misconfigured test is a failure

- Never skip a test. If a test cannot run because the system is misconfigured
  (a missing API key, OAuth token, config file, dependency or service), it
  **fails** with a message saying what to fix. Do not use `pytest.skip`,
  `skipif`, `xfail` or `importorskip` to get around it.
- Never report a test as successful unless it ran and passed. A skipped,
  deselected or not-run test is not a pass: say so plainly ("N tests did not
  run") and list them. Do not write "all tests pass" when any did not run.
- Tests that are deliberately opted out by marker (for example `-m expensive`)
  must be named as not run whenever you report test results.
