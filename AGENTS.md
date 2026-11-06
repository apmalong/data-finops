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

## Development workflow
All development follows Matt Pocock's engineering skills (`mattpocock-skills` plugin). Tasks live only in GitHub Issues (`docs/agents/issue-tracker.md`); `docs/implementation/progress.md` is the per-milestone verification log, not a task list.
1. **Design** (when terms, scope or architecture change): `grill-with-docs`, updating `GLOSSARY.md` and ADRs inline. Unresolved decisions go to `docs/implementation/open-questions.md`.
2. **Spec:** take the next unfinished milestone in `docs/implementation/plan.md` and run `to-spec`: one spec issue per milestone, labelled `ready-for-agent`, with test seams agreed with the owner.
3. **Tickets:** `to-tickets` splits the spec into tracer-bullet vertical slices, published as sub-issues of the spec with native blocked-by links and the `ready-for-agent` label. Work the frontier: tickets whose blockers are all closed.
4. **Build:** `implement`, one ticket per branch. Use `tdd` (red, then green) only at the seams agreed for that ticket. Run type checks and single test files often and the full suite at the end, then run `code-review` against `main`. Commit with `Closes #<ticket>`.
5. **Standards gate:** before any collector, parser, serializer, lineage model, telemetry format, allocation algorithm or integration, apply ADR 0014 (see the section below).
6. **Pull request:** body follows the `pr` skill (Summary, Evidence, Merge Danger). `main` is protected: a PR and a passing `quality` check are required.
7. **Documentation:** every ticket names the Diátaxis documentation type it changes (tutorial, how-to, reference, explanation, or none) in a `## Documentation` section, and done includes that change (ADR 0025, spec 07). The code-review Spec reviewer checks it against the diff.
8. **Close a milestone** only with runnable verification: record commands and output in `progress.md` and update affected docs.
9. Bugs use `diagnosing-bugs`; issue intake uses `triage` with the labels in `docs/agents/triage-labels.md`.

## Repository conventions
`src/data_finops/{contracts,billing,normalization,ledger,identity,lineage,dbt,workload,attribution,api,providers}`; contracts are generated into `schemas/` by `uv run python -m data_finops.schemas`; `scripts/` holds operator tools and network-dependent upstream checks, never product code; `infra/pulumi/aws`; `tests/{unit,integration,contract}`; `examples/demo`; `site/` (the user documentation site, deployed as committed; ADR 0025); `scripts/site/` (its dev tooling, outside the wheel); `examples/reference-workload` (ADR 0021: separate uv project, never imported by `data_finops`).

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
