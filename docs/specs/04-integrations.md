# Integration contracts

## Core ports (not built)
- `BillingSource.list_records(window, cursor)`
- `QueryHistory.list_executions(window)`
- `LineageReceiver.accept(event)`
- `DbtArtifactReader.parse(manifest, run_results)`
- `IdentityResolver.resolve(executions, runs, assets)`
- `CostAllocator.allocate(pools, evidence, policy)`
- `CostLedger.reconcile()`

All adapters support capability discovery, an explicit permission list, health checks, a watermark, pagination, rate limits with backoff, and provenance. Unknown fields are kept in metadata. Schema evolution is backward compatible.

**Status (drift review #207, 2026-11-07):** none of these ports exists. With one provider, the seams are plain functions: `billing.focus.load_export` and `billing.aws_data_exports.load_delivery`, `WorkloadStore.ingest_*`, `calculation.run_calculation`, `attribution.engine.allocate` and `Ledger.reconcile`. Pagination and the watermark exist only in the AWS collectors (`providers.aws`, `collection`). The capability-declared ports of ADR 0011 wait for a second provider.

## AWS Athena MVP

### Billing
For the demo, read FOCUS billing exports from local Parquet or CSV. Then read the AWS native FOCUS export (Data Exports) in S3, as the customer configures it (ADR 0015). CUR 2.0 through `focus_converters` is deferred.

### Query history
Get the Athena history through the AWS API, or from a query history extract that the customer gives. A plain BatchGetQueryExecution response is accepted, with the account and the Region given next to it (#31). At implementation time, verify the API retention and permissions.

A query history file is limited to 512 MiB, and it must be a BatchGetQueryExecution response. An entry that fails validation is reported by position and reason, and its content is not shown.

A stored Query Execution keeps only allowlisted metadata (#61):
- identity, workgroup, statement type, catalog and database, state and times
- error category, error type, and whether the error is retryable
- numeric statistics and engine version

Query text, `ExecutionParameters`, `StateChangeReason`, error messages and unknown fields are removed.

### Correlation
Correlate through the Athena query execution ID and the dbt query comment, where they are supported. Do not assume that generic dbt artifacts contain Athena query IDs.

The default dbt query comment has `node_id` but not `invocation_id`. This was verified in `DEFAULT_QUERY_COMMENT` of dbt-adapters (#23). Thus operators must configure a `query-comment` that includes both IDs. The exact configuration is in `examples/reference-workload/README.md`.

dbt-athena sends the comment as one line, `-- /* {json} */`, before the statement. It sends a statement that starts with `alter`, `drop`, `optimize`, `vacuum` or `msck` without the comment (OQ-22). One dbt model can issue more than one Athena query.

data-finops reads the comment only from the first line of each query. It stores only its `node_id` and `invocation_id`, never the query text (M3). The `invocation_id` and `node_id` of the Trusted Query Comment are stored next to the metadata (as `dbt_query_comment`). They are part of the digest of the observation. Thus two reports of one query that name different Model Executions are kept separate, and they get a flag (#63).

Price and charge reconciliation is specific to each provider.

## Airflow / OpenLineage MVP

### Events
Accept OpenLineage START, COMPLETE, FAIL and ABORT RunEvents. Tolerate other event types. JobEvent and DatasetEvent are reported as unsupported. Validate each event against the pinned RunEvent schema. Each event is limited to 1 MiB. Keep the namespace, job, run and parent facets, and the dataset inputs and outputs.

Support offline event files as a JSON array or JSON Lines (the format that the file transport of `dbt-ol` writes, #31). M2 ingests only files.

### The HTTP receiver (M5)
The M5 HTTP receiver takes one event in each request at `POST /api/v1/lineage`. That is the default endpoint of the OpenLineage HTTP transport. The receiver screens the event through the same path as a file item:
- 201: accepted or a duplicate, with the ingest counts
- 202: unsupported (JobEvent, DatasetEvent)
- 400: rejected, with the kind and the reason but no content
- 413: above 1 MiB, before the event is parsed
- 415: a body with a `Content-Encoding`. Compressed bodies are out of scope (#107).
- 401: without the configured token

Arrays and JSON Lines are for files only. Ingress and authentication follow ADR 0024 (OQ-6).

### What is kept
Before storage, these fields are removed: the `query` of the `sql` facet, `errorMessage.stackTrace`, and each `extractionError.errors[].stackTrace`. All other facets are kept exactly as they are. SQL quoted inside an error message is not detected.

Rejected events are never stored. The ingest result reports each rejected event by position and reason, and its content is not shown. dbt artifacts are limited to 512 MiB. So is an OpenLineage event file, local or in the Inbox (#214). A larger file is rejected whole at position 0, before it is read, and nothing of it is stored.

### Airflow
Do not require Airflow itself to run for the demo.

Parent runs get to dbt only if the DAG gives them to `dbt-ol`. The DAG must give the `lineage_parent_id` and `lineage_root_parent_id` macros of the provider as `OPENLINEAGE_PARENT_ID` and `OPENLINEAGE_ROOT_PARENT_ID`. This was observed with Airflow 3.3.2 and provider 2.20.1 (#75). The DAG is in `examples/reference-workload/airflow/`. Then dbt-ol reports its invocation run in the OpenLineage namespace of Airflow. Its model runs keep `dbt`.

## dbt MVP
Parse `manifest.json` and `run_results.json` with version checks. Link node IDs, invocation IDs, status, `timing` and dependencies. Later, data-finops can read Elementary tables that already exist, as an option. Do not require an Elementary installation.

## Later adapters
- GCP: Cloud Billing export, BigQuery INFORMATION_SCHEMA jobs and reservations. Keep on-demand pricing separate from capacity pricing.
- dlt: pipeline traces, load info and destination IDs.
- Dagster and Prefect: native run and asset APIs, and OpenLineage if it is there.
- OTel: receive spans with run IDs and cloud resource identities. Do not correlate arbitrary traces.
- BI: QuickSight and Tableau metadata and consumption, with warehouse query IDs where they are available. Do not assume that dashboard views are equal to warehouse spend.

## Data protection
- Use a metadata allowlist.
- In exported demos, remove or redact SQL literals, tokens, PII and customer identifiers.
- Query collectors extract the correlation comment from the query text, and remove the text before persistence.
- To keep query text, the operator must opt in explicitly.
- By default, the ingestion API is private. Authenticate production ingress.
- Do not require write access to the data planes of the customer.
