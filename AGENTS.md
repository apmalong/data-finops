# Agent instructions

You are implementing the Data FinOps project specified in this repository. Treat specifications and ADRs as authoritative. Do not silently expand scope. If implementation reveals contradictions, document an ADR amendment and proceed with the smallest safe interpretation.

## Engineering rules
- Python 3.12+, typed code, `pytest`, `ruff`, `mypy` (or pyright), deterministic calculations using `Decimal` for currency. No floating-point monetary arithmetic.
- Build an **offline synthetic end-to-end path first**. Never require real cloud credentials for unit/integration tests.
- The cloud bill is authoritative; telemetry explains allocation. Never invent exact query costs from duration or time overlap.
- Separate billed amounts, estimated allocations, and potential savings. Preserve provenance and method for every allocation.
- Reconcile per source billing line/cost pool, currency, period and adjustment version. Prevent double-counting on replay.
- Avoid exporting credentials, raw SQL containing sensitive literals, customer data, or tenant identifiers to an external service by default.
- Optional integrations must fail open: telemetry outage must not fail production ETL.
- No third-party source copied without per-file license check, original notices, source URL, pinned commit, modifications, and provenance entry.
- Prefer official APIs and SDK dependencies over copying application internals.
- Do not claim an upstream code audit is complete; prior evaluation was preliminary and no exact SHAs or test executions were verified.
- Do not make automatic infrastructure changes from optimization suggestions.

## Implementation workflow
1. Read `docs/implementation/plan.md` and pick next unfinished milestone.
2. Write failing acceptance tests for that milestone.
3. Implement minimal code with explicit interfaces.
4. Run lint, types, unit and integration tests; record outputs.
5. Update documentation and `docs/implementation/progress.md` with changes and limitations.
6. Never mark a phase complete without runnable verification.

## Repository conventions
`src/data_finops/{billing,identity,lineage,dbt,attribution,api,providers}`; `infra/pulumi/aws`; `tests/{unit,integration,contract}`; `examples/demo`.

## Security
Use read-only IAM for ingestion, separate deployment permissions, secrets from runtime identity/secret manager, bound query limits, no public ingestion endpoint by default, and no direct SQL interpolation of untrusted metadata.

## Before M0: mandatory M-1 bootstrap
Read `docs/implementation/M-1-bootstrap.md`, `docs/implementation/golden-fixtures.md`, `docs/implementation/execution-permissions.md`, and `docs/implementation/architecture-boundaries.md`. First generate and commit a reviewed `uv.lock` using Python 3.12, then run quality gates. The current fixture tests check arithmetic and structure only; do not treat them as an implementation.

## Standards-first implementation gate (ADR 0014)
Before writing a collector, parser, serializer, lineage model, telemetry format, allocation algorithm, or new integration, inspect `docs/research/standards-conformance-matrix.md` and `docs/research/build-vs-reuse-register.md`. Evaluate maintained standards and implementations, update the register, and record a reasoned reuse/build decision. Prefer official schemas, SDKs, and APIs. Pin and test versions; preserve extensions; never describe unverified conformance as complete. A new custom wire format or mandatory third-party service requires an ADR amendment.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for apmalong/data-finops (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
