# ADR 0021: A reference workload for validation against real billing

Status: Accepted (2026-11-01).

## Context
The FOCUS export account used for M1 verification (#12) does not necessarily run the data engineering workloads this product attributes. Without real dbt-on-Athena runs and their OpenLineage events, M2–M4 (workload metadata, query correlation, Coverage-based metering) can only be validated on synthetic fixtures, and questions that only real billing can answer stay open: how Athena on-demand charges appear in FOCUS (OQ-3), the real `ConsumedUnit`, and whether Athena lines carry the workgroup as `ResourceId`.

## Decision
1. Build a small **reference workload** in the owner's AWS account, used only to generate real billing lines, query history, dbt artifacts and OpenLineage events for validation.
2. Scope: an S3 bucket, a Glue database, an Athena workgroup (on-demand, with a per-query scan limit as a cost guardrail), and a tiny dbt-athena project run with `dbt-ol` so OpenLineage events are written to files. dbt's `query-comment` carries `node_id` and `invocation_id` (spec 04). Local Airflow is deferred until M3 needs the parent-run link.
3. It lives in `examples/reference-workload/` as its own uv project, so its heavy dependencies (Pulumi, dbt, OpenLineage) never enter the `data_finops` package (ADR 0009). Infrastructure is Pulumi Python (ADR 0002) with a local file state backend, so no hosted control plane is required (ADR 0001).
4. Cost guardrails beyond the per-query scan limit, such as an AWS Budget alert, are an optional setting each installation configures (amount and recipient), never hard-coded.
5. It is a test harness, not product: separate from the product's own optional AWS deployment (M6). Every `pulumi up`, workload run and `pulumi destroy` requires the owner's explicit approval (execution permissions).

## Consequences
Small, real costs: Athena bills per data scanned with a per-query minimum, so tiny models cost a fraction of a cent per run; S3 and Glue costs are negligible at this size. Real outputs are anonymized (#18) before they are used as fixtures. New dependencies for the harness go through the build-vs-reuse register.

## Alternatives rejected
- Synthetic fixtures only: cannot answer OQ-3 or validate Coverage against real Athena billing.
- Using an existing production workload: customer data and access risks; not reproducible.
- Full stack including Airflow from the start: more setup than M2 needs.
