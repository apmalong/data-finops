# Integration contracts

## Core ports
`BillingSource.list_records(window, cursor)`; `QueryHistory.list_executions(window)`; `LineageReceiver.accept(event)`; `DbtArtifactReader.parse(manifest, run_results)`; `IdentityResolver.resolve(executions, runs, assets)`; `CostAllocator.allocate(pools, evidence, policy)`; `CostLedger.reconcile()`.

All adapters support capability discovery, explicit permission list, health checks, watermark, pagination, rate-limit/backoff, and provenance. Unknown fields preserved in metadata; schema evolution is backward compatible.

## AWS Athena MVP
Read FOCUS billing exports from local Parquet/CSV for demo, then the customer-configured AWS native FOCUS export (Data Exports) in S3 (ADR 0015). CUR 2.0 via `focus_converters` is deferred. Query Athena history via AWS API or customer-provided query history extract. Verify API retention and permissions at implementation time. Correlation via Athena query execution ID and dbt query comment where supported; don't assume generic dbt artifacts contain Athena query IDs. The default dbt query comment carries `node_id` but not `invocation_id` (*verify*), so operators must configure a `query-comment` that includes both; document the exact config with the adapter. One dbt model may issue several Athena queries. Price/charge reconciliation is provider-specific.

## Airflow / OpenLineage MVP
Accept HTTP OpenLineage events (START, COMPLETE, FAIL, ABORT and others tolerated), validate event schema, persist namespace/job/run/parent facets and dataset IO. Support offline JSON event files. Avoid requiring Airflow itself to run for demo.

## dbt MVP
Parse `manifest.json` and `run_results.json` with version checks. Link node IDs, invocation IDs, status, timing and dependencies. Optional existing Elementary tables later; do not require Elementary installation.

## Later adapters
- GCP: Cloud Billing export, BigQuery INFORMATION_SCHEMA jobs and reservations; distinguish on-demand vs capacity pricing.
- dlt: pipeline traces/load info and destination IDs.
- Dagster/Prefect: native run/asset APIs and OpenLineage if present.
- OTel: receive spans with run IDs and cloud resource identities, avoid arbitrary trace correlation.
- BI: QuickSight/Tableau metadata and consumption, with warehouse query IDs where available; no assumption dashboard views equal warehouse spend.

## Data protection
Metadata allowlist; strip/redact SQL literals, tokens, PII, and customer identifiers in exported demos. Query collectors extract the correlation comment from query text and discard the text before persistence; retaining query text is an explicit operator opt-in. Ingestion API private by default; authenticate production ingress. Do not require write access to customer data planes.
