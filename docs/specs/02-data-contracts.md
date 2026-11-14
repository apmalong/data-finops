# Canonical data contracts

Typed Pydantic models in `src/data_finops` are the source of truth for contracts. The JSON Schemas in `schemas/` are generated from them. CI fails if the committed schemas drift from the models, and the golden fixtures must validate against the schemas. Terms follow `GLOSSARY.md`.

## Entities
- `BillingLine` (ADR 0015): one FOCUS row for the pinned FOCUS release, with FOCUS column names.
  - It includes the billing account, the Billing Period start and end, and the charge period start and end (UTC). It also includes the charge category, the billing currency, the service and the SKU.
  - It has the billed usage quantity and unit, and **every** FOCUS cost column (for example BilledCost and EffectiveCost) as Decimal.
  - Credits, refunds and discounts are lines of their own (Adjustments), never fields.
  - Provider extensions keep their FOCUS `x_` names.
  - It also records the Export Snapshot that it came from, and its position in that snapshot.
- `ExportSnapshot`: source, billing account, Billing Period, publication and ingestion time, raw file hashes. It is the revision unit for billing.
- Cost Pools: not a published contract. The pools of the engine (`attribution.engine`: metered, direct, shared, idle) have a `pool_id` and the weights. Each line is partitioned separately (ADR 0016), and each Cost Basis is split with the same weights.
- `WorkloadRun`: run_id, system, namespace, parent_run_id and root_run_id (from the OpenLineage ParentRunFacet only), start and end, status, flags, attempt, trace_id and metadata. attempt, trace_id and metadata are optional until a source gives them. The per-model runs of dbt-ol are Workload Runs too (ADR 0022).
  - status: `running`, `completed`, `failed`, `aborted` or `conflicting`. It comes from the terminal event, in any arrival order. Two different terminal events give `conflicting`.
  - flags: `conflicting_observations` (the same run, event type and time, with different content), `conflicting_jobs` and `conflicting_parents`.
- `ModelExecution`: node_id and invocation_id from the dbt run results, with the status value of dbt as reported.
  - Its start and end come from the `timing` steps of dbt.
  - Its provenance is the SHA-256 of the run-results file.
  - Its flags: `conflicting_observations` when more than one run-results file was ingested for the invocation.
- `DbtNode`: a manifest node, for each invocation and manifest file. It has the node_id, resource type, name, package, relation name, materialization and `depends_on.nodes` of the node, with provenance and the same flag. It has no SQL, descriptions, columns or macros.
- `QueryExecution`: provider, account_id, region, query_execution_id, workgroup, statement_type, state, submitted_at and completed_at, scanned_bytes, billable_bytes, the IDs of the correlation comment, provenance and flags.
  - The versioned provider policy computes billable_bytes.
  - The IDs of the correlation comment are extracted (see spec 04, data protection).
  - A reservation ID and other workload metadata are not stored yet.
- `DataAsset`: namespace and name, as the OpenLineage "Naming Conventions" give them for datasets. Kind and metadata are not stored yet.
- `LineageEdge`: a Workload Run and a Data Asset that it read (`input`) or wrote (`output`), observed_at and provenance. Edges between two Data Assets are not stored. Column-level lineage is the eventual goal (owner, 2026-11-07).
- `IdentityLink` (M3): source, target, evidence_type, evidence_value and deterministic policy version.
  - Ambiguous links and links that conflict are recorded, and they are never used for allocation.
  - Links are derived when they are asked for, and they are returned with their Link Outcome.
  - A Model Execution target is identified as the compact JSON array `["<invocation_id>","<node_id>"]`.
  - Evidence values are compact JSON objects with sorted keys. They name the facet fields that they come from.
- `Allocation`: billing_line_ref, pool_id, target_type and target_id, amount (Decimal), currency, cost basis, category, method, confidence, coverage, policy_version, calculation_run_id, evidence_refs and as_of.
  - target_type: a Query Execution, `idle` or `unallocated` (ADR 0016).
  - category: direct, shared, idle or unallocated.
  - confidence: direct_verified, metered_estimate or heuristic_estimate. It is absent when the category is unallocated.
  - coverage: for metered shares.
- `AllocationSnapshot`: calculation_run_id, policy_version, input digest (Export Snapshots and telemetry), created_at and is_current.

## Identity and time
All timestamps are ISO-8601 UTC. Keep the provider region for API queries. Never equate a dbt `unique_id` with a specific run. Join `invocation_id` and the execution or run context. Give identifiers a namespace across accounts and providers.

## Financial accounting
Credits, tax, refunds, discounts and the blended, unblended and amortized cost bases are not interchangeable. Each FOCUS cost column reconciles independently, and a ledger view selects one. The default view is BilledCost, and you can select EffectiveCost (ADR 0015). Negative adjustments are valid.

Convert currency only with an explicit dated FX source. Never sum across currencies implicitly. Currency codes are ISO 4217.

Some providers deliver amounts as binary floating point, for example the AWS FOCUS `double` columns. The adapter converts each of these amounts one time, through its shortest round-trip decimal representation. This also sets the scale of the line. The raw value is kept in provenance (OQ-21). That provenance is the documented project extension `data_finops_raw_values` on each Billing Line: the exact `repr()` string of each converted double, keyed by column path. It is not a FOCUS column, and it is never used in arithmetic.

## Storage and versioning
Keep the raw immutable import hashes, the normalized records, the attribution policy version, the source ingestion time and the calculation run ID. Billing revisions are whole Export Snapshots. A restatement replaces the snapshot for its billing account, Billing Period and source, and the earlier snapshots are kept.

Any period can be calculated again at any time. Each Calculation Run makes a new Allocation Snapshot and moves the current pointer, and all earlier snapshots are kept. Period close for chargeback is deferred.
