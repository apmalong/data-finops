# Attribution and reconciliation algorithm

## Principles
1. Financial source is authoritative; execution metadata does not generate money.
2. A cost pool has one accounting basis and currency, and must be partitioned exactly once.
3. Explicit query IDs outrank tag or time-based heuristics. No match solely on temporal overlap.
4. Distinguish billed cost from estimated allocation, and attributable cost from marginal cost.
5. Idle and unallocated are first-class synthetic targets.

## Deterministic pipeline
A. Import billing rows, deduplicate by source key/version, select current revision.
B. Normalize provider and SKU, classify pool and eligibility. Non-eligible billing stays unallocated.
C. Ingest OpenLineage events idempotently keyed by event/run identity and content digest; ingest dbt artifacts by invocation and artifact hash.
D. Resolve exact provider query ID to dbt node execution via trusted query-comment mapping or query-history linkage; resolve dbt invocation to Airflow run via explicit parent facets / run mapping. Store evidence and confidence.
E. Allocate direct charges only where provider pricing/billing permits a defensible line-to-query match. When billing is aggregated, apportion an eligible pool by measured billed bytes or provider-specific meter with method `estimated_metered_share` and keep reconciliation adjustment separate. Do not treat proportional weights as exact invoices.
F. For shared pool, allocate by documented nonnegative weights over aligned time intervals; if no observed activity, allocate to `idle`; if ambiguous, `unallocated`. If weights only partially cover activity, record coverage and allocate unknown remainder according to explicit policy.
G. Quantize at currency minor units using largest-remainder deterministic tie-breaker by stable target ID; enforce sum allocations == pool total including negative adjustments.
H. Materialize ledger atomically under policy/input version; replay replaces equivalent results rather than appending duplicate financial allocations.

## Pseudocode
```
for pool in eligible_cost_pools:
  evidence = resolve_exact_links(pool, telemetry)
  shares = calculate_weights(pool, evidence, policy)
  if shares missing: shares = {UNALLOCATED: 1}
  amounts = allocate_decimal_with_deterministic_remainders(pool.amount, shares)
  assert sum(amounts) == pool.amount
  upsert_allocation_snapshot(pool, policy_version, input_digest, amounts)
```

## Confidence vocabulary
`direct_verified` (billing or provider-level linkage), `metered_estimate` (validated usage weights), `heuristic_estimate` (opt-in, never automatic for MVP), `unallocated`. Confidence is categorical evidence quality, not a fabricated probability.

## Special cases to test
Retries, failed jobs, zero-billed queries, canceled queries, missing/duplicate events, multiple parents, concurrent runs, shared queries, partial billing periods, negative credits, currency mismatch, daylight savings, late corrections, and zero usage weights.
