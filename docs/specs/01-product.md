# Product requirements and scope

## Personas
Data platform engineer, data reliability engineer, platform owner, FinOps analyst. The deployment is customer-managed, and a subscription is not necessary.

## MVP user journeys
1. Start the local demo with one command. Examine the Cost Summary, the Roll-ups to Model Executions, the Unallocated and Idle amounts, the lineage and the provenance (spec 06).
2. Import synthetic AWS billing lines and Athena query executions. Ingest OpenLineage run events and the dbt `manifest.json` and `run_results.json`.
3. Use explicit IDs to resolve the identity of a query, its dbt model and its Airflow run deterministically. If the IDs are not there, show the costs that are not attributed.
4. Explain each Allocation from the Billing Line through the Query Execution and the Model Execution to the Workload Run. This is the Cost Explanation (spec 06). Data products are deferred (ADR 0016).
5. Run the same inputs again, and make sure that the allocated costs do not increase. Apply a corrected billing snapshot, which replaces the earlier one deterministically.
6. After the demo passes, configure the optional AWS ingestion and deploy through Pulumi.

## MVP scope
For direct job attribution, the MVP allocates only AWS Athena **on-demand query scan charges**. It also has generic cost pools (shared, idle and unallocated) for the demonstration. Other AWS cost categories stay unallocated until explicit allocators exist.

The MVP does not say that Athena billed bytes always map 1:1 to invoice charges. Free tiers, minimums, discounts, credits, rounding, pricing changes and billing adjustments are important.

The MVP also includes:
- the Airflow and OpenLineage run hierarchy
- dbt artifact ingestion
- query-ID correlation through provider metadata or query comments, when they are available
- an HTML API and dashboard
- JSON and Parquet fixture ingestion
- a local SQLite store (ADR 0018), and optional Postgres later

## Next releases (interfaces only in MVP)
- GCP: BigQuery on-demand and capacity/reservation allocation, GCS storage, Cloud Run, Dataflow
- AWS: S3 storage, ECS/Fargate, Glue
- data platforms and orchestrators: Snowflake, dlt, Dagster, Prefect
- telemetry: OpenTelemetry
- BI tools: QuickSight, Tableau, Looker, Power BI
- Kubernetes: OpenCost/K8s
- features: optimizer recommendations, unit economics, policy guardrails

## Nonfunctional
- The offline demo takes 5 minutes or less on a developer laptop, after the dependencies are installed.
- Event ingestion is idempotent, and replay is deterministic. Memory for fixtures is bounded. Timestamps are UTC, and currency is explicit. Unknown cost is never allocated in a hidden way.
- You can configure the ingestion lookback, and when data is processed again. A lineage service that is always on is not mandatory.
- The cost overhead is measurable and documented. Do not claim an AWS install of less than 15 minutes until it is measured.

## Success measures
- 100% of the eligible fixture billing cost is reconciled.
- Repeated ingestion does not duplicate a cost.
- Each displayed amount is traceable.
- Ambiguous matches stay unallocated.
- All third-party code has license provenance.
- The test suite passes offline.
