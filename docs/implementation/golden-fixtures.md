# Golden fixture semantics

- `fixtures/golden/scenarios.json` is an independent acceptance oracle, not output from the application.
- Existing billing fixtures: the $6 Athena line goes to `q-001` as a metered estimate (ADR 0017), on the stated assumption that its billed usage quantity equals `q-001`'s Billable Usage (Coverage = 1); it rolls up to `model.demo.orders`. The $4 ineligible line stays Unallocated. The FOCUS fixture encodes it: ConsumedQuantity 0.001 TB against `q-001`'s 1,000,000,000 bytes scanned (decimal units). Whether Athena bills exactly the scanned bytes, and the real ConsumedUnit string, are verified at M4.
- Independent synthetic scenario: direct $3, shared $2 (1.25 / 0.75), idle $1, unknown $4. These are explicitly fabricated values to test the allocation engine.
- Negative adjustment: a -$1 credit is signed; do not clamp to zero.
- Reconciliation is per Billing Line, currency, cost basis, period and Export Snapshot (ADR 0016); a simple aggregate equality is necessary but insufficient.
- Allocation targets are Query Executions, `idle` or `unallocated`; model and run figures are Roll-ups.
- Build future independent fixtures for duplicate replay, whole-snapshot restatement, retries, ambiguous joins, weighted interval allocation, refunds, currency separation, Coverage below and above one, sub-cent lines, and several queries per model.
- Every scenario carries its own billing lines, so reconciliation is checked per line, and every allocation validates against the generated Allocation schema. The existing-fixture scenario is cross-checked against the FOCUS export.
- At M4, tests MUST call the actual attribution engine and compare its output with these scenarios. Current bootstrap tests intentionally do not do so.
- `fixtures/reference-workload/` is an anonymized real sample from the reference workload (#24, #32, #35), not an oracle: contract tests check the correlation it shows (comments to run results and manifest nodes, one model with several queries, the uncommented `alter table` statements of OQ-22). Provenance and anonymization are in its README.
