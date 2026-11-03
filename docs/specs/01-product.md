# Product requirements and scope

## Personas
Data platform engineer, data reliability engineer, platform owner, FinOps analyst. Customer-managed deployment, no subscription required.

## MVP user journeys
1. Launch local demo with one command; inspect the Cost Summary, Roll-ups to Model Executions, Unallocated and Idle amounts, lineage and provenance (spec 06).
2. Import synthetic AWS billing lines and Athena query executions; ingest OpenLineage run events and dbt `manifest.json` / `run_results.json`.
3. Resolve deterministic query-to-dbt-to-Airflow identity using explicit IDs; present unattributed costs when IDs absent.
4. Explain any Allocation from Billing Line through Query Execution and Model Execution to Workload Run (the Cost Explanation, spec 06; data products are deferred, ADR 0016).
5. Re-run same inputs without increasing allocated costs; apply corrected billing snapshot with deterministic replacement.
6. Configure optional AWS ingestion and deploy via Pulumi after demo passes.

## MVP scope
AWS Athena **on-demand query scan charges** only for direct job attribution, plus generic shared/idle/unallocated cost pools for demonstration. Other AWS cost categories remain unallocated until explicit allocators exist. No assertion that Athena billed bytes always map 1:1 to invoice charges: free tiers, minimums, discounts, credits, rounding, pricing changes and billing adjustments matter.

Airflow/OpenLineage run hierarchy; dbt artifact ingestion; query-ID correlation via provider metadata/query comments when available; HTML API/dashboard; JSON/Parquet fixture ingestion; SQLite local store (ADR 0018); optional Postgres later.

## Next releases (interfaces only in MVP)
GCP BigQuery on-demand and capacity/reservation allocation, S3/GCS storage, ECS/Fargate, Glue, Cloud Run, Dataflow, Snowflake, dlt, Dagster, Prefect, OpenTelemetry, QuickSight, Tableau, Looker, Power BI, OpenCost/K8s, optimizer recommendations, unit economics, policy guardrails.

## Nonfunctional
- Offline demo <= 5 minutes on developer laptop after dependencies installed.
- Idempotent event ingestion, deterministic replay, bounded memory for fixtures, UTC timestamps, explicit currency, no hidden allocation of unknown cost.
- Configurable ingestion lookback and reprocessing; zero mandatory always-on lineage service.
- Cost overhead measurable and documented; no claim of <15-minute AWS install until measured.

## Success measures
100% of eligible fixture billing cost reconciled; no duplicate cost under repeated ingestion; all displayed amounts traceable; ambiguous matches remain unallocated; no third-party code with missing license provenance; test suite passes offline.
