# Attribution and reconciliation algorithm

## Principles
1. Financial source is authoritative; execution metadata does not generate money.
2. A cost pool has one accounting basis and currency. Each Billing Line in it is partitioned exactly once; the pool only supplies the weights (ADR 0016).
3. Explicit query IDs outrank tag or time-based heuristics. No match solely on temporal overlap.
4. Distinguish billed cost from estimated allocation, and attributable cost from marginal cost.
5. Idle and unallocated are first-class synthetic targets.

## Deterministic pipeline
A. Import billing as Export Snapshots (ADR 0015). A restatement replaces the whole snapshot for its (billing account, Billing Period, source); prior snapshots are retained. Rows are never deduplicated; every row is a distinct Billing Line, and replay is detected per snapshot (ADR 0019). The current snapshot for a Snapshot Scope is the latest publication (`published_at`, ties broken by snapshot ID), independent of import order; an older publication with new content imported late is retained as history and reported as superseded, while content equal to the current snapshot is a replay (ADR 0020).
B. Normalize provider and SKU, classify pool and eligibility. Non-eligible billing stays unallocated. Adjustment lines (credits, refunds, discounts) are Unallocated in the MVP and reported as their own labelled amount (ADR 0017).
C. Ingest OpenLineage events idempotently keyed by (runId, eventType, eventTime) plus content digest; an event with the same key and a different digest is retained as a separate observation, never merged silently. Ingest dbt artifacts by invocation and artifact hash.
D. Resolve exact provider query ID to dbt Model Execution (node and invocation) via the trusted query comment; resolve dbt invocation to Airflow run via the OpenLineage ParentRunFacet. Store evidence. One Model Execution may own several Query Executions; a Query Execution linking to more than one Model Execution is ambiguous and makes no Identity Link. Only a Trusted Query Comment at the very start of the query text is Evidence; only its `node_id` and `invocation_id` are kept, and the query text is never stored. Resolve each dbt-ol model run to its Model Execution by the `dbt_node.unique_id` job facet and `dbt_run.invocation_id` run facet (ADR 0022). Each attempt has a Link Outcome (linked, no_evidence, unmatched, ambiguous); a comment naming a (node, invocation) that no run results report is unmatched and links to nothing. Links are derived from stored observations when queried, so arrival order does not matter.
E. Allocate direct charges only where provider pricing/billing permits a defensible line-to-query match. When billing is aggregated, compute Coverage per Billing Line (ADR 0017): known Billable Usage divided by the line's billed usage quantity. Coverage = 1: split the line by Billable Usage. Coverage < 1: split the covered fraction, put the remainder in Unallocated, record Coverage. Coverage > 1: the whole line is Unallocated and flagged. Method `estimated_metered_share`, category `direct`, confidence `metered_estimate`. Do not treat proportional weights as exact invoices. Charges for failed or retried work go to that Query Execution with its status (ADR 0016).
F. For shared pool, allocate by documented nonnegative weights over aligned time intervals; if no observed activity, allocate to `idle`; if ambiguous, `unallocated`. If weights only partially cover activity, record coverage and allocate unknown remainder according to explicit policy.
G. Quantize each line's allocations at that line's own decimal scale, never coarser than the currency minor unit, using largest remainder with a deterministic tie-break by stable target ID. For a negative amount, apply this to the absolute value and restore the sign. Enforce sum of allocations == line amount for every Billing Line, including negative adjustments.
H. Materialize an Allocation Snapshot atomically under policy/input version and move the current pointer; replay with identical inputs and policy reproduces the identical snapshot rather than appending duplicate financial allocations. Earlier snapshots are retained.

## Pseudocode
```
for pool in eligible_cost_pools:
  evidence = resolve_exact_links(pool, telemetry)
  for line in pool.lines:
    shares = calculate_weights(line, pool, evidence, policy)   # includes coverage remainder
    if shares missing: shares = {UNALLOCATED: 1}
    amounts = allocate_decimal_with_deterministic_remainders(line.amount, shares, scale=line.scale)
    assert sum(amounts) == line.amount
  write_allocation_snapshot(policy_version, input_digest, all_amounts)
```

## Confidence vocabulary
`direct_verified` (billing or provider-level linkage), `metered_estimate` (validated usage weights with measured Coverage), `heuristic_estimate` (opt-in, never automatic for MVP). Confidence applies only to allocated amounts; Unallocated amounts carry no confidence. Confidence is categorical evidence quality, not a fabricated probability.

## Special cases to test
Retries, failed jobs, zero-billed queries, canceled queries, missing/duplicate events, multiple parents, concurrent runs, shared queries, partial billing periods, negative credits, currency mismatch, daylight savings, late corrections, zero usage weights, Coverage below, equal to and above one, sub-cent lines, several queries per Model Execution, and whole-snapshot restatement.
