# Acceptance and verification matrix

## Must-pass tests
- Billing import is lossless for source keys, signed amounts, currency and accounting basis.
- Duplicate billing rows and OpenLineage events do not inflate costs.
- Corrected billing version yields a single current ledger snapshot and retained history.
- dbt model is identified by node + invocation, not node alone.
- Each explicit Athena query ID links to exactly one Model Execution (node + invocation); a Model Execution may own several Query Executions.
- Missing, conflicting or ambiguous evidence produces unallocated, never guessed direct attribution.
- Every Billing Line satisfies `sum(allocation.amount) == line.amount`, including idle, unallocated, credits, sub-cent amounts and rounding (ADR 0016).
- Metered shares follow Coverage (ADR 0017): Coverage < 1 leaves the uncovered remainder Unallocated; Coverage > 1 leaves the whole line Unallocated and flagged.
- A billing restatement replaces the whole Export Snapshot and yields a new current Allocation Snapshot with history retained.
- Different currencies and billing bases are never summed without explicit conversion/selection.
- Negative adjustments, zero usage, failed runs, retries, concurrent runs and late events covered.
- API validates filters and pagination; no raw SQL/secrets in public response.
- UI labels direct vs estimated costs and exposes evidence, policy version and reconciliation delta.
- Synthetic demo runs offline; collector outage does not affect pipeline runs.
- Third-party copied code has complete provenance and license notices.

## Quality gates
`ruff check .`, `mypy src` (or pyright), `pytest -q`, schema validation, security dependency scan, Pulumi preview policy tests when implemented. Report commands, versions and failures in progress log.

## Demo scenario
Airflow DAG `daily_orders` runs dbt invocation `inv-001`; dbt model `model.demo.orders` executes Athena query `q-001`; model `model.demo.customers` has no query link. Billing totals $10.00: a $6.00 eligible Athena line whose billed usage quantity equals `q-001`'s Billable Usage (Coverage = 1), and a $4.00 ineligible line. `q-001` receives $6.00 as a metered estimate, which rolls up to `model.demo.orders` and `daily_orders`; $4.00 stays Unallocated; `model.demo.customers` receives nothing. Exactly $10.00 is accounted for. Fixture numbers are synthetic and not representative of AWS pricing.
