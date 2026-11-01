# Milestone plan and task ordering

## Standards-first gate (required at M-1 and before each integration)
Review ADR 0014, update the standards conformance matrix and build-versus-reuse register; pin upstream schemas and add contract tests as implemented. Never count an unverified upstream version as supported.

## M0: scaffold and contracts
Create `pyproject.toml`, package, CI, format/type/test setup, Pydantic models, JSON schema validation, fixture loader, provenance docs. Generate `schemas/` from the models with a CI drift check (spec 02). Rebuild fixtures in provider-native form: FOCUS billing lines with usage quantity, Athena executions carrying the query comment, OpenLineage events with parent and dbt facets, dbt artifacts valid against pinned schemas. **Exit:** `pytest` passes without cloud credentials; golden fixtures validate against generated schemas. **Status:** complete 2026-11-01 (progress.md).

## M1: offline billing ledger
Pin the FOCUS release AWS emits (ADR 0015). Load fixture Export Snapshots, retain every cost column, deduplicate within a snapshot and persist. Build ledger with Decimal and per-line reconciliation. **Exit:** zero delta per Billing Line, replay idempotent, restatement replaces the Export Snapshot and retains the previous one.

## M2: workload metadata
Parse dbt manifest/run_results, ingest OpenLineage JSON/HTTP events, build run/model/asset graph. **Exit:** expected fixture lineage and statuses; invalid payload rejected.

## M3: query correlation
Load Athena query history fixture, link explicit query IDs to dbt model execution and parent Airflow run. **Exit:** exact links match, missing/ambiguous IDs stay unallocated.

## M4: allocation engine
Implement Coverage-based Athena on-demand metering (ADR 0017) with Billable Usage from a versioned provider policy, and shared/idle/unallocated pools; per-line rounding at line scale, method/evidence, Allocation Snapshots. **Exit:** all accounting invariants and negative adjustment tests pass; tests call the engine and match the golden scenarios.

## M5: API and HTML demo
FastAPI `/health`, `/v1/costs/summary`, `/v1/allocations`, `/v1/lineage`, `/v1/reconciliation`, `/v1/provenance`; static dashboard and explain-cost drilldown. **Exit:** local demo walkthrough passes.

## M6: AWS deployment (optional after M5)
Pulumi Python components, private API, scheduled read-only ingestion, encrypted storage, cost limits, deployment docs and teardown. **Exit:** preview policies validated; deployment only with explicit user authorization and credentials.

## Deferred
GCP, Snowflake, dbt Elementary optional tables, dlt, Dagster, OTel, BI connectors, automatic optimization, multi-account aggregation, enterprise auth.

## Work decomposition
Each milestone must have issue-sized tasks: models, adapter, tests, docs. Prefer vertical runnable increments to scaffolding all deferred integrations.
