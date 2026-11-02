# Milestone plan and task ordering

## Standards-first gate (required at M-1 and before each integration)
Review ADR 0014, update the standards conformance matrix and build-versus-reuse register; pin upstream schemas and add contract tests as implemented. Never count an unverified upstream version as supported.

## M0: scaffold and contracts
Create `pyproject.toml`, package, CI, format/type/test setup, Pydantic models, JSON schema validation, fixture loader, provenance docs. Generate `schemas/` from the models with a CI drift check (spec 02). Rebuild fixtures in provider-native form: FOCUS billing lines with usage quantity, Athena executions carrying the query comment, OpenLineage events with parent and dbt facets, dbt artifacts valid against pinned schemas. **Exit:** `pytest` passes without cloud credentials; golden fixtures validate against generated schemas. **Status:** complete 2026-11-01 (progress.md).

## M1: offline billing ledger
Pin the FOCUS release AWS emits (ADR 0015). Load fixture Export Snapshots, retain every cost column and persist to SQLite (ADR 0018); read Parquet exports with pyarrow; no row deduplication (ADR 0019). Build ledger with Decimal and per-line reconciliation. **Exit:** zero delta per Billing Line, replay idempotent, restatement replaces the Export Snapshot and retains the previous one.

## M2: workload metadata
Parse dbt manifest/run_results, ingest OpenLineage event files (JSON array or JSON Lines; HTTP moves to M5), build the run/model/asset graph in the workload store (ADR 0018 amendment, ADR 0022). Identity Links wait for M3. **Complete 2026-11-01** (#40, #41–#48; exit criterion recorded in progress.md). **Exit:** expected fixture lineage and statuses; invalid payload rejected.

## M3: query correlation
Load Athena query history fixture, link explicit query IDs to dbt model execution and parent Airflow run. Identity Links are derived when queried, never inferred from time, workgroup or order (ADR 0006): Query Execution → Model Execution by Trusted Query Comment, dbt-ol model run → Model Execution and dbt invocation → Workload Run by dbt-ol facets (ADR 0022). **Exit:** exact links match; missing or ambiguous Evidence makes no Identity Link and its Link Outcome is reported (allocation of unlinked Query Executions is M4's, ADR 0016).

## M4: allocation engine
Implement Coverage-based Athena on-demand metering (ADR 0017) with Billable Usage from a versioned provider policy, and shared/idle/unallocated pools; per-line rounding at line scale, method/evidence, Allocation Snapshots. **Exit:** all accounting invariants and negative adjustment tests pass; tests call the engine and match the golden scenarios.

## M5: API and HTML demo
FastAPI OpenLineage receiver (reusing M2's ingest path; OQ-6), `/health`, `/v1/costs/summary`, `/v1/allocations`, `/v1/lineage`, `/v1/reconciliation`, `/v1/provenance`; static dashboard and explain-cost drilldown. **Exit:** local demo walkthrough passes.

## Reference workload (supports M2–M4 validation; ADR 0021)
Pulumi Python stack in `examples/reference-workload/` (S3, Glue database, Athena workgroup with a per-query scan limit) and a tiny dbt-athena project run with `dbt-ol`, as its own uv project. Produces real Athena billing lines, query history, dbt artifacts and OpenLineage events, anonymized before use. Airflow deferred until M3 needs it. **Exit:** stack previews and deploys only with owner approval; a run produces artifacts and events; Athena charges appear in the FOCUS export; teardown verified.

## M6: AWS deployment (optional after M5)
Pulumi Python components, private API, scheduled read-only ingestion, encrypted storage, cost limits, deployment docs and teardown. **Exit:** preview policies validated; deployment only with explicit user authorization and credentials.

## Deferred
GCP, Snowflake, dbt Elementary optional tables, dlt, Dagster, OTel, BI connectors, automatic optimization, multi-account aggregation, enterprise auth.

## Work decomposition
Each milestone must have issue-sized tasks: models, adapter, tests, docs. Prefer vertical runnable increments to scaffolding all deferred integrations.
