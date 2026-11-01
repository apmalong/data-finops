# M-1 — Reproducible bootstrap and quality gates

## Done when
- [x] `uv.lock` committed after a successful `uv lock` with Python 3.12 and network access.
- [x] `uv sync --extra dev --frozen` works on a clean machine.
- [x] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, and `uv run pytest` pass.
- [x] `uv run pip-audit` and secret scanning complete; review any alerts.
- [x] Golden fixtures checked independently against a written financial interpretation.
- [ ] Git initialized, initial commit created, and GitHub branch protection configured by repository owner.
- [x] CI runs successfully on pull request.

## Scope and caveats
Scaffold tests validate fixture shape and golden *arithmetic*, not an implemented allocation engine. M1–M4 must implement the algorithms and verify actual computed allocations against golden expectations. Do not mark financial reconciliation complete based only on these tests.

The dependency lockfile is intentionally not fabricated. Run `uv lock` in a network-enabled environment, inspect resolved packages, and commit `uv.lock` before enabling frozen CI. Pre-commit hook revisions are examples and should be reviewed before installation.
