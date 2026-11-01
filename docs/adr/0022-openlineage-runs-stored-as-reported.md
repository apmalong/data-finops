# ADR 0022: OpenLineage runs are stored as reported and linked, never merged

Status: Accepted (2026-11-01).

## Context
dbt-ol reports each dbt model as its own OpenLineage run (job = the model, parent = the dbt run job), carrying `dbt_node.unique_id` and `dbt_run.invocation_id`. dbt's own run results report the same work as Model Executions (node and invocation). Recording both risks counting one unit of work twice, and merging them at ingest would hide which source said what.

## Decision
Every OpenLineage run is stored as a Workload Run exactly as reported, including dbt-ol's per-model runs, with its direct parent and root from the ParentRunFacet. Model Executions come only from dbt run results. The two are connected only by an Identity Link (M3) whose Evidence is the event's `dbt_node.unique_id` and `dbt_run.invocation_id`. Nothing is merged at ingest; Roll-ups (M4) follow Identity Links, so no unit of work is counted twice.

## Consequences
The workload store keeps one record per source observation and stays a faithful copy of what producers sent. Queries that want "the model's execution" must follow Identity Links rather than read a merged row. A dbt run without dbt-ol, or dbt-ol without run results, still yields usable records on one side.

## Alternatives rejected
- Merge dbt-ol model runs into Model Executions at ingest: simpler queries, but loses provenance and makes conflicting reports impossible to see.
- Ignore dbt-ol per-model runs: loses the asset inputs and outputs only OpenLineage reports.
