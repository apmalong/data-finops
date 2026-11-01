# Canonical data contracts

Typed Pydantic models in `src/data_finops` are the source of truth for contracts. The JSON Schemas in `schemas/` are generated from them; CI fails if the committed schemas drift from the models, and the golden fixtures must validate against them. Terms follow `GLOSSARY.md`.

## Entities
- `BillingLine` (ADR 0015): one FOCUS row for the pinned FOCUS release, using FOCUS column names. Includes billing account, Billing Period start/end, charge period start/end (UTC), charge category, billing currency, service, SKU, billed usage quantity and unit, and **every** FOCUS cost column (for example BilledCost and EffectiveCost) as Decimal. Credits, refunds and discounts are their own lines (Adjustments), never fields. Provider extensions keep their FOCUS `x_` names. Also records the Export Snapshot it came from and its position within it.
- `ExportSnapshot`: source, billing account, Billing Period, publication/ingestion time, raw file hashes. The revision unit for billing.
- `CostPool`: grouped eligible billing lines with currency, time window, cost basis and pricing category. Supplies weights; each line is partitioned separately (ADR 0016).
- `WorkloadRun`: run_id, system, namespace, parent_run_id and root_run_id (from the OpenLineage ParentRunFacet only), start/end, status (`running`, `completed`, `failed`, `aborted`, `conflicting`: from the terminal event whatever the arrival order; two different terminal events are `conflicting`), attempt, trace_id, metadata (optional until a source provides them). dbt-ol's per-model runs are Workload Runs too (ADR 0022).
- `ModelExecution`: node_id and invocation_id from dbt run results, with dbt's own status value as reported.
- `QueryExecution`: provider, account/project, region, query_execution_id, start/end, status, scan_bytes, Billable Usage (computed by the versioned provider policy), workgroup, correlation comment (extracted; see spec 04 data protection), reservation_id, workload metadata.
- `DataAsset`: namespace and name following the OpenLineage dataset naming convention, kind, metadata.
- `LineageEdge`: upstream/downstream, relation type, observed_at, provenance.
- `IdentityLink`: source, target, evidence_type, evidence_value, deterministic policy version. Ambiguous or conflicting links are recorded and never used for allocation.
- `Allocation`: billing_line_ref, pool_id, target_type/id (Query Execution, `idle` or `unallocated`; ADR 0016), amount Decimal, currency, cost basis, category (direct/shared/idle/unallocated), method, confidence (direct_verified/metered_estimate/heuristic_estimate; absent when category is unallocated), coverage (for metered shares), policy_version, calculation_run_id, evidence_refs, as_of.
- `AllocationSnapshot`: calculation_run_id, policy_version, input digest (Export Snapshots and telemetry), created_at, is_current.

## Identity and time
All timestamps ISO-8601 UTC; preserve provider region for API queries. Never equate dbt `unique_id` with a specific run: join `invocation_id` and execution/run context. Namespace identifiers across accounts and providers.

## Financial accounting
Credits, tax, refunds, discounts and blended/unblended/amortized cost bases are not interchangeable. Each FOCUS cost column reconciles independently; a ledger view selects one. The default view is BilledCost, with EffectiveCost selectable (ADR 0015). Negative adjustments are valid. Currency conversion only with an explicit dated FX source; no implicit cross-currency sums. Currency codes are ISO 4217. Provider amounts delivered as binary floating point (for example AWS FOCUS `double` columns) are converted once, at the adapter, through their shortest round-trip decimal representation, which also fixes the line's scale; the raw value is kept in provenance (OQ-21). That provenance is the documented project extension `data_finops_raw_values` on each Billing Line: the exact `repr()` string of every converted double, keyed by column path. It is not a FOCUS column and is never used in arithmetic.

## Storage and versioning
Keep raw immutable import hashes, normalized records, attribution policy version, source ingestion time, and calculation run ID. Billing revisions are whole Export Snapshots: a restatement replaces the snapshot for its (billing account, Billing Period, source), and prior snapshots are retained. Any period may be recalculated at any time; each Calculation Run produces a new Allocation Snapshot and moves the current pointer, retaining all earlier snapshots. Period close for chargeback is deferred.
