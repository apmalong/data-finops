# Canonical data contracts

Schemas in `schemas/` are normative examples; implementation may use Pydantic equivalents while preserving semantics.

## Entities
- `BillingLine`: provider, billing_account, source_record_id, source_version, service, SKU, usage_start/end UTC, currency, billed_cost Decimal, credits/adjustments (separate), source_uri, provider_metadata. Stable unique key includes source and version.
- `CostPool`: grouped eligible billing lines with currency, time window, cost basis and pricing category.
- `WorkloadRun`: run_id, system, namespace, parent_run_id, start/end, status, trace_id, metadata.
- `QueryExecution`: provider, account/project, region, query_execution_id, start/end, billed_bytes if available, scan_bytes, workgroup, query_comment, reservation_id, workload metadata.
- `DataAsset`: namespace, stable identifier, kind, name, metadata.
- `LineageEdge`: upstream/downstream, relation type, observed_at, provenance.
- `IdentityLink`: source, target, evidence_type, evidence_value, confidence, deterministic policy version.
- `Allocation`: pool_id, billing_source_refs, target_type/id, amount Decimal, currency, method, confidence, policy_version, calculation_run_id, evidence_refs, category (direct/shared/idle/unallocated), as_of.

## Identity and time
All timestamps ISO-8601 UTC; preserve provider region for API queries. Never equate dbt `unique_id` with a specific run: join `invocation_id` and execution/run context. Namespace identifiers across accounts and providers.

## Financial accounting
Credits, tax, refunds, discounts and blended/unblended/amortized cost bases are not interchangeable. Choose one configured reporting basis per ledger view. Negative adjustments are valid. Currency conversion only with an explicit dated FX source; no implicit cross-currency sums.

## Storage and versioning
Keep raw immutable import hashes, normalized records, attribution policy version, source ingestion time, and calculation run ID. Version corrected inputs; expose current snapshot and historical audit trail.
