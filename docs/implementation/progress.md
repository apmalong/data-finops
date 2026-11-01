# Progress log

2026-11-01: Initial design package created. No code implementation, upstream code extraction, live cloud deployment, or test execution performed. Next: M0.

## M-1 bootstrap (2026-11-01)
Scaffold, CI workflow, fixture arithmetic tests, golden scenarios, execution permission guidance, and architecture boundaries added. NOT complete: lockfile generation, actual test runs, remote repository setup, CI validation and real allocation engine.

## M-1 execution (2026-11-01)
Run locally on Windows 11 with uv 0.12.23 and CPython 3.12.15 (uv-managed). No cloud credentials used.

- `uv lock --python 3.12`: resolved 54 packages; dev-only (no runtime dependencies). Direct: bandit 1.9.4, build 1.6.1, hypothesis 6.170.14, mypy 1.20.2, pip-audit 2.10.1, pre-commit 4.7.0, pytest 9.2.0, pytest-cov 7.1.0, ruff 0.17.3. All from PyPI.
- `rm -rf .venv && uv sync --extra dev --frozen`: succeeded from the lockfile alone; `uv lock --check` passes.
- First gate run: mypy, pytest, pip-audit, bandit passed; ruff failed (6 lint errors, 4 files unformatted). Fixed: `AllocationKind` now uses `StrEnum` (UP042), fixture-loader lambda replaced with a `def` (E731), import sorting and long lines via `ruff check --fix` / `ruff format`.
- Final gate run:
  - `uv run ruff check .`: All checks passed!
  - `uv run ruff format --check .`: 55 files already formatted
  - `uv run mypy src`: Success: no issues found in 11 source files
  - `uv run pytest --cov=data_finops`: 4 passed; coverage 100% (16 statements, contracts only)
  - `uv run pip-audit`: No known vulnerabilities found (local package skipped: not on PyPI)
  - `uv run bandit -q -r src`: no issues
- Secret scan (gitleaks v8.24.2 via Docker): `git` mode over history: no leaks found. `dir` mode reported 200 findings, all under `.venv/Lib/site-packages/license_expression` (third-party license data, gitignored); zero findings in project files.
- Golden fixtures, checked independently by script against `golden-fixtures.md`:
  - `existing-fixtures-no-price-evidence`: per-line reconciliation holds against `fixtures/billing.json` (bill-001 6.00, bill-002 4.00, both unallocated; no query price inferred).
  - `explicit-synthetic-cost-pools`: direct 3.00, shared 2.00 split 1.25/0.75 (62.5%/37.5%), idle 1.00, unknown 4.00; total 10.00.
  - `negative-adjustment`: -1.00 retained signed, not clamped.
  - Gap: synthetic and credit records have no corresponding billing lines with period/source_version, so only aggregate reconciliation is checkable for those scenarios. The doc states aggregate equality is insufficient; add billing-line fixtures for them before M4.

- CI on PR #1: first run failed at gitleaks (shallow checkout could not resolve the PR commit range); fixed with `fetch-depth: 0`. Both push and pull_request runs then passed.

Still open: GitHub branch protection (repository owner).

## Pre-implementation architecture review (2026-11-01)
Domain modeling and design review with the repository owner, following the grill-with-docs workflow. No production code written.
- Added `GLOSSARY.md` (domain language) and `docs/research/architecture-review.md` (12 internal contradictions, 10 standards conflicts, 9 proposals; now a historical record).
- Decided OQ-9 to OQ-19 and narrowed OQ-1 (`open-questions.md`); OQ-20 open, non-blocking.
- New ADRs: 0015 (FOCUS billing lines, export-snapshot revisions), 0016 (allocate to Query Executions, roll-ups above), 0017 (metered share requires measured Coverage).
- Updated specs 02–04, `acceptance.md`, `golden-fixtures.md`, `plan.md`, demo walkthrough, both schemas, the standards matrix and the golden scenario. The demo and golden oracle no longer conflict: the $6 Athena line is a metered estimate to `q-001` at Coverage = 1.
- Moved to M0: fixture rebuild in provider-native form and contracts generated from Pydantic models.
