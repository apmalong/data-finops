# Acceptance and verification matrix

## Must-pass tests
- Billing import is lossless for source keys, signed amounts, currency and accounting basis.
- Duplicate billing rows and OpenLineage events do not inflate costs.
- Corrected billing version yields a single current ledger snapshot and retained history.
- dbt model is identified by node + invocation, not node alone.
- Explicit Athena query ID links to one execution and one dbt model in synthetic fixtures.
- Missing, conflicting or ambiguous evidence produces unallocated, never guessed direct attribution.
- Every cost pool satisfies `sum(allocation.amount) == pool.amount`, including idle, unknown, credits and rounding.
- Different currencies and billing bases are never summed without explicit conversion/selection.
- Negative adjustments, zero usage, failed runs, retries, concurrent runs and late events covered.
- API validates filters and pagination; no raw SQL/secrets in public response.
- UI labels direct vs estimated costs and exposes evidence, policy version and reconciliation delta.
- Synthetic demo runs offline; collector outage does not affect pipeline runs.
- Third-party copied code has complete provenance and license notices.

## Quality gates
`ruff check .`, `mypy src` (or pyright), `pytest -q`, schema validation, security dependency scan, Pulumi preview policy tests when implemented. Report commands, versions and failures in progress log.

## Demo scenario
Airflow DAG `daily_orders` runs dbt invocation `inv-001`; dbt model `model.demo.orders` executes Athena query `q-001`; model `model.demo.customers` has no query link. Billing pool $10.00 includes $6.00 eligible query activity and $4.00 unallocated/shared; ensure exactly $10.00 accounted, without falsely assigning second model's cost. Fixture numbers are synthetic and not representative of AWS pricing.
