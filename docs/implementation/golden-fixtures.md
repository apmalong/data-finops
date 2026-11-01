# Golden fixture semantics

- `fixtures/golden/scenarios.json` is an independent acceptance oracle, not output from the application.
- Existing billing fixtures: $6 Athena and $4 unknown shared charges are *unallocated*. Query size and a dbt node ID do not establish a $6 direct query cost.
- Independent synthetic scenario: direct $3, shared $2 (1.25 / 0.75), idle $1, unknown $4. These are explicitly fabricated values to test the allocation engine.
- Negative adjustment: a -$1 credit is signed; do not clamp to zero.
- Reconciliation is per bill line/cost pool, currency, period and source version; a simple aggregate equality is necessary but insufficient.
- Build future independent fixtures for duplicate replay, changed versions, retries, ambiguous joins, weighted interval allocation, refunds and currency separation.
- At M4, tests MUST call the actual attribution engine and compare its output with these scenarios. Current bootstrap tests intentionally do not do so.
