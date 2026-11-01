# ADR 0013: Offline-first reproducible development and golden oracles
Status: Accepted

## Decision
Use Python 3.12, uv lockfile, Ruff, mypy, pytest, Hypothesis and CI. Start without cloud credentials. Maintain independent golden allocations using Decimal string amounts and explicit evidence semantics. CI must fail if the lockfile is absent or stale.

## Consequences
Initial lock generation requires network access; do not claim fully reproducible builds until the lockfile has been generated and committed. The scaffold's fixture tests are not a substitute for implementation-level reconciliation tests.
