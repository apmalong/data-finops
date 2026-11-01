# Golden fixture semantics

- `fixtures/golden/scenarios.json` is an independent acceptance oracle, not output from the application.
- Existing billing fixtures: the $6 Athena line goes to `q-001` as a metered estimate (ADR 0017), on the stated assumption that its billed usage quantity equals `q-001`'s Billable Usage (Coverage = 1); it rolls up to `model.demo.orders`. The $4 ineligible line stays Unallocated. The M0 fixture rebuild must encode that quantity in FOCUS form; until then the assumption lives only here.
- Independent synthetic scenario: direct $3, shared $2 (1.25 / 0.75), idle $1, unknown $4. These are explicitly fabricated values to test the allocation engine.
- Negative adjustment: a -$1 credit is signed; do not clamp to zero.
- Reconciliation is per Billing Line, currency, cost basis, period and Export Snapshot (ADR 0016); a simple aggregate equality is necessary but insufficient.
- Allocation targets are Query Executions, `idle` or `unallocated`; model and run figures are Roll-ups.
- Build future independent fixtures for duplicate replay, whole-snapshot restatement, retries, ambiguous joins, weighted interval allocation, refunds, currency separation, Coverage below and above one, sub-cent lines, and several queries per model.
- Gap: the synthetic and credit scenarios have no corresponding billing lines, so only aggregate reconciliation is checkable for them, and the synthetic scenario still targets models rather than Query Executions. Fix both in the M0 rebuild, when the fixtures move to the generated Allocation contract.
- At M4, tests MUST call the actual attribution engine and compare its output with these scenarios. Current bootstrap tests intentionally do not do so.
