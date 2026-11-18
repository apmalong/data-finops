# Attribution and reconciliation algorithm

## Principles
1. The financial source is authoritative. Execution metadata does not make money.
2. A cost pool has one accounting basis and one currency. Each Billing Line in it is partitioned exactly once. The pool only gives the weights (ADR 0016).
3. Explicit query IDs come before tag heuristics and time heuristics. Never match only on temporal overlap.
4. Keep billed cost separate from estimated allocation, and attributable cost separate from marginal cost.
5. Idle and unallocated are first-class synthetic targets.

## Deterministic pipeline

**A. Import billing as Export Snapshots (ADR 0015).**
- A restatement replaces the whole snapshot for its billing account, Billing Period and source. Earlier snapshots are kept.
- Rows are never deduplicated. Each row is a distinct Billing Line, and replay is detected for each snapshot (ADR 0019).
- The current snapshot for a Snapshot Scope is the latest publication (`published_at`, with ties broken by snapshot ID). The import order does not change it.
- If an older publication with new content is imported late, it is kept as history and reported as superseded. Content equal to the current snapshot is a replay (ADR 0020).

**B. Normalize the provider and the SKU, and classify the pool and the eligibility.**
- Billing that is not eligible stays unallocated.
- In the MVP, Adjustment lines (credits, refunds, discounts) are Unallocated. They are reported as their own labeled amount (ADR 0017).

**C. Ingest OpenLineage events idempotently.**
- The key is (runId, eventType, eventTime), plus a content digest.
- An event with the same key and a different digest is kept as a separate observation. It is never merged silently.
- Ingest dbt artifacts by invocation and artifact hash.

**D. Resolve identities, and store the Evidence.**
- Resolve the exact provider query ID to the dbt Model Execution (node and invocation) through the Trusted Query Comment.
- Resolve the dbt invocation to the Airflow run through the OpenLineage ParentRunFacet.
- One Model Execution can own more than one Query Execution. A Query Execution that links to more than one Model Execution is ambiguous, and it makes no Identity Link.
- Only a Trusted Query Comment at the very start of the query text is Evidence. Only its `node_id` and `invocation_id` are kept. The query text is never stored.
- Resolve each dbt-ol model run to its Model Execution (ADR 0022). Use the `dbt_node.unique_id` job facet and the `dbt_run.invocation_id` run facet.
- Each attempt has a Link Outcome: linked, no_evidence, unmatched or ambiguous. A comment that names a (node, invocation) that no run results report is unmatched, and it links to nothing.
- Links are derived from stored observations when they are queried. Thus the arrival order is not important.

**E. Allocate direct charges.**
- Allocate direct charges only where the provider billing permits a defensible match from a line to a query.
- When billing is aggregated, compute Coverage for each Billing Line (ADR 0017): the known Billable Usage divided by the billed usage quantity of the line.
  - Coverage = 1: split the line by Billable Usage.
  - Coverage < 1: split the covered fraction, put the remainder in Unallocated, and record Coverage.
  - Coverage > 1: the whole line is Unallocated, and it gets a flag.
- Method `estimated_metered_share`, category `direct`, confidence `metered_estimate`.
- Do not treat proportional weights as exact invoices.
- Charges for failed or retried work go to that Query Execution, with its status (ADR 0016).

**F. Allocate shared pools.**
- For a shared pool, allocate by documented weights that are not negative, over aligned time intervals.
- If there is no observed activity, allocate to `idle`: an explicit idle pool (M4 structure 1).
- If the split is ambiguous, or if all the weights are zero, allocate to `unallocated` (`no_evidence`, M4 structure 10). When all the weights are zero, nothing gives a reason for a split.
- If the weights cover only part of the activity, record the coverage. Then allocate the unknown remainder by an explicit policy.

**G. Quantize.**
- Quantize the allocations of each line at the decimal scale of that line. Never use a scale coarser than the currency minor unit.
- Use the largest remainder method, with a deterministic tie-break by stable target ID.
- For a negative amount, apply this to the absolute value, then restore the sign.
- For each Billing Line, enforce that the sum of the allocations is equal to the line amount. This includes negative adjustments.

**H. Make the Allocation Snapshot.**
- Make an Allocation Snapshot atomically, under the policy and input version, and move the current pointer.
- A replay with identical inputs and policy makes the identical snapshot again. It does not add duplicate financial allocations.
- Earlier snapshots are kept.

## M4 structure (decided 2026-11-02, implemented in #81–#90, and the Athena policies checked against a real export in #91)

1. **Pools.**
   - Athena on-demand lines are metered by Coverage (step E).
   - Adjustments are Unallocated (step B).
   - Each other line is Unallocated as `ineligible`.
   - Shared, direct and idle pools are explicit inputs to the engine, and the golden synthetic scenario uses them. Nothing in the MVP makes them automatically from billing.

2. **Shape.**
   - The engine in `attribution` is pure. Its inputs are Billing Lines, Query Executions with Billable Usage, pools and policy. Its outputs are Allocations. It has no I/O.
   - Billable Usage is a versioned provider policy in `providers.athena_billing` (#82).
   - A thin Calculation Run reads the Ledger and the workload store, calls the engine and stores the result. It is in its own package, `calculation`, because `attribution` must not import the Ledger, the workload store or provider policies (#88).
   - The Calculation Run classifies each line (step B, `line-classification/1`):
     - FOCUS `Credit` and `Adjustment` lines are Adjustments.
     - A usage line is metered if two conditions are true. It is not a correction. The Athena policy recognizes it as an on-demand scan charge (a positive ConsumedQuantity in a known unit).
     - All other lines are Ineligible and reported. These include tax, purchases, corrections and other services.
   - A metered line without a usage account or Region has nothing in scope (Coverage 0).
   - A billed query with unknown Billable Usage is not included in the known usage, and it is reported.
   - Allocations record one policy version. It joins with `+` the versions of the engine, the line classification, the two Athena policies and Cost Dimension resolution (`cost-dimensions/1`, spec 09).
   - The input digest covers the Export Snapshot ID, and the reference and pool of each line. It also covers the Billable Usage and Evidence of each Query Execution in scope. Last, it covers the queries whose usage is unknown, and that version.

3. **Scope.**
   - The Query Executions that count toward the Coverage of a line are the billing scope of the line. This is an accounting scope, not Evidence (ADR 0023).
   - Query Executions without a link count, and they get Metered Shares.
   - The keys (`providers.athena_billing.in_billing_scope`, #83, #91):
     - the usage account of the line (SubAccountId, not the BillingAccountId that pays) and its Region
     - the workgroup, when the ResourceId of the line is an Athena workgroup ARN
     - the completion time of the query in the charge period (start inclusive), for any number of hours in the period
   - The real export (#91) confirmed the workgroup ARN, and showed one line of five hours.
   - It also billed two queries in the line of the next hour, although they completed seconds before that hour. Thus the completion time is not always the billed timestamp (OQ-25).
   - `athena-billing-scope/2` keys the pool label by the charge period of the line. `/1` keyed it by day.

4. **Storage.**
   - The `ledger` module stores Allocation Snapshots in the same SQLite file (ADR 0018). Each has an `input_digest` over the Export Snapshot, the Query Executions used and the policy versions.
   - A Calculation Run with an identical digest makes nothing. New inputs make a new snapshot and move the current pointer (step H).
   - One snapshot is current for each Snapshot Scope: the snapshot of the latest Calculation Run.
   - As for Export Snapshots (ADR 0020), only the inputs of the current snapshot are a replay. Inputs equal to the inputs of a superseded snapshot make a new snapshot (`Ledger.store_allocations`, #87).

5. **Cost columns.** Each FOCUS cost column (BilledCost, EffectiveCost, ListCost, ContractedCost) is allocated with the same weights. Each Cost Basis reconciles on its own.

6. **Roll-ups.**
   - Roll-ups to Model Executions and Workload Runs are pure sums over the Identity Links of M3, in `attribution`. They are never Allocations.
   - A Query Execution without a link rolls up to nothing.
   - The Calculation Run resolves the links one time. It records them as the Evidence of each Allocation:
     - the Model Execution (`dbt-query-comment:<invocation>:<node>`)
     - each Workload Run that its invocation was reported under: the run of the invocation, its parents and its root, each one time. This includes a parent or root that is known only from a ParentRunFacet (`openlineage-run:<run>`).
   - Roll-ups read only that Evidence (`attribution.rollups`, #89). Thus a stored snapshot always rolls up in the same way when it is read. New OpenLineage events change the input digest, as each other link does.
   - The per-model runs of dbt-ol are the same work as their Model Execution (ADR 0022). They are not Roll-up targets.

7. **Test seams.** The pure engine (golden scenarios and accounting invariants), and the Calculation Run on a temporary SQLite file.

8. **Athena Billable Usage (`athena-billable-usage/3`, #310).**
   - The bill's `TB` is 2^40 bytes. No AWS source states the unit. `/3` is fitted to the owner's real lines (`docs/research/m7-athena-billing-units.md`).
   - Each query that succeeded or was canceled and scanned bytes: its raw `DataScannedInBytes` / 2^40, with a minimum of 10^-5 TB.
   - The queries of each UTC hour of completion are added. That sum is rounded down to a multiple of 5 × 10^-6 TB.
   - Each query of the hour gets a part of the rounded sum, in proportion to its raw quantity. The last query of the hour gets the remainder, so the hour adds up exactly.
   - The rules below for failed, 0-byte, queued and unknown queries stay as in `/2`.
   - `/2` (#91) read `TB` as 10^12 bytes. It rounded each query up to the next MB (10^6 bytes), with a minimum of 10 MB. It stays callable, as `/1` does.
   - Failed queries: 0.
   - A query that scanned 0 bytes: 0, for each statement type. The real export billed nothing for 0-byte SELECTs, an INSERT, a VACUUM and a reused result. The AWS documentation says that a reused result always reports 0 bytes. This was observed only for a 0-byte original.
   - Under `/2`, each other query that succeeded or was canceled: `DataScannedInBytes` rounded up to the next MB, with a minimum of 10 MB. The export billed the minimum each for a CTAS, an INSERT and an OPTIMIZE that scanned a few hundred bytes.
   - `/1` charged the minimum for each 0-byte query except DDL (OQ-24). `/1` and `/2` stay callable (`billable_bytes(query, version)`), so you can compute again what such a Calculation Run recorded. A Calculation Run always uses the current versions.
   - StatementType alone never decides, because Athena labels CTAS as `DDL`.
   - Queued queries and queries in progress bill 0 in that observation. A billed query that reported no scan has unknown Billable Usage (`providers.athena_billing`, #82).
   - The minimum-only lines of #91 fit decimal and binary units alike. The owner's demo lines of 2026-11 fit only binary units (#310).

9. **Coverage of one.** Compare the known Billable Usage with the ConsumedQuantity of the line, in the last digit that ConsumedQuantity was exported with. If they are different by half a unit of that digit or less, Coverage counts as exactly 1. If not, the exact Decimal is recorded.

10. **Unallocated reasons.** An Unallocated amount names its reason in `method`:
    - `ineligible`: not a metered pool
    - `adjustment`: a credit, refund or discount
    - `coverage_remainder`: the part that is not covered when Coverage < 1, or the whole line when no Query Execution is known
    - `coverage_exceeded`: Coverage > 1, with a flag
    - `no_evidence`: an explicit shared pool with no positive weight, so nothing gives a reason for a split (#86, from the golden oracle)

    Allocated amounts also name their method. These are `estimated_metered_share` (metered), `provider_line_match` (direct) and `weighted_share` (shared). For Idle, the method is `no_observed_activity`.

11. **Real-export checks (#91, done 2026-11-07).**
    - These were checked against the 2026-11 FOCUS export and the saved history of the reference workgroup (OQ-3): units, reused results, CTAS and INSERT, OPTIMIZE and VACUUM, failed queries, ResourceId, and lines of more than one hour.
    - Under `/2`, the 2026-11-01 and 2026-11-06 lines get to Coverage 1.
    - Not yet observed: a reused result of a query that scanned data, and two workgroups billed in one hour. It is inferred from ResourceId that each workgroup has its own rows.

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
- `direct_verified`: linkage at the billing or provider level.
- `metered_estimate`: validated usage weights with measured Coverage.
- `heuristic_estimate`: opt-in. It is never automatic for the MVP.

Confidence applies only to allocated amounts. Unallocated amounts have no confidence. Confidence is a category of Evidence quality, not a fabricated probability.

## Special cases to test
- retries, failed jobs, queries billed at zero, canceled queries
- events that are not there, duplicate events, multiple parents, concurrent runs, shared queries
- partial billing periods, negative credits, currency mismatch, daylight savings, late corrections
- zero usage weights, Coverage below, equal to and above one, lines below one cent
- more than one query for each Model Execution, and restatement of a whole snapshot
