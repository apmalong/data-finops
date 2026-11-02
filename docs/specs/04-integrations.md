# Integration contracts

## Core ports
`BillingSource.list_records(window, cursor)`; `QueryHistory.list_executions(window)`; `LineageReceiver.accept(event)`; `DbtArtifactReader.parse(manifest, run_results)`; `IdentityResolver.resolve(executions, runs, assets)`; `CostAllocator.allocate(pools, evidence, policy)`; `CostLedger.reconcile()`.

All adapters support capability discovery, explicit permission list, health checks, watermark, pagination, rate-limit/backoff, and provenance. Unknown fields preserved in metadata; schema evolution is backward compatible.

## AWS Athena MVP
Read FOCUS billing exports from local Parquet/CSV for demo, then the customer-configured AWS native FOCUS export (Data Exports) in S3 (ADR 0015). CUR 2.0 via `focus_converters` is deferred. Query Athena history via AWS API or customer-provided query history extract; a plain BatchGetQueryExecution response is accepted with the account and region supplied alongside it (#31). Verify API retention and permissions at implementation time. Correlation via Athena query execution ID and dbt query comment where supported; don't assume generic dbt artifacts contain Athena query IDs. The default dbt query comment carries `node_id` but not `invocation_id` (verified in dbt-adapters' `DEFAULT_QUERY_COMMENT`, #23), so operators must configure a `query-comment` that includes both; the exact config is in `examples/reference-workload/README.md`. dbt-athena sends the comment as a single line, `-- /* {json} */`, before the statement, and sends statements starting with `alter`, `drop`, `optimize`, `vacuum` or `msck` without it (OQ-22). One dbt model may issue several Athena queries. A query history file is capped at 512 MiB and must be a BatchGetQueryExecution response; an entry that fails validation is reported by position and reason without echoing its content. The comment is read only from the first line of each query; only its `node_id` and `invocation_id` are stored, never the query text (M3). A stored Query Execution keeps only allowlisted metadata (identity, workgroup, statement type, catalog and database, state, times, error category and type, numeric statistics, engine version); query text, `ExecutionParameters`, `StateChangeReason`, error messages and unknown fields are dropped (#61). The Trusted Query Comment's `invocation_id` and `node_id` are stored beside that metadata (as `dbt_query_comment`) and are part of the observation's digest, so two reports of one query naming different Model Executions are kept apart and flagged (#63). Price/charge reconciliation is provider-specific.

## Airflow / OpenLineage MVP
Accept OpenLineage RunEvents (START, COMPLETE, FAIL, ABORT and others tolerated; JobEvent and DatasetEvent reported as unsupported), validate each against the pinned RunEvent schema, cap each event at 1 MiB, persist namespace/job/run/parent facets and dataset IO. Support offline event files as a JSON array or JSON Lines (what `dbt-ol`'s file transport writes, #31). M2 ingests files only; the HTTP receiver comes with the M5 API and reuses the same ingest path, and OQ-6 (authentication and ingress) is decided then. Before storage, the `sql` facet's `query` and `errorMessage.stackTrace` are removed; all other facets are kept verbatim. Rejected events are never stored: the ingest result reports each by position and reason without echoing its content. dbt artifacts are capped at 512 MiB. Avoid requiring Airflow itself to run for demo.

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
