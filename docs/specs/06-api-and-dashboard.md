# HTTP API and dashboard (M5)

Decided in the M5 design session (2026-11-03). The `reporting` read model answers each read. `api` is a thin FastAPI adapter over it (architecture-boundaries.md). The OpenLineage receiver is in spec 04. Ingress and the token are in ADR 0024.

## Which snapshot a read serves
By default, each read serves the current Allocation Snapshot of each Snapshot Scope.

Filters:
- `billing_account_id` and `billing_period`, on each snapshot read.
- `source` and `calculation_run_id`, on each snapshot read except the Cost Summary. Use `calculation_run_id` to read a superseded snapshot.

The Cost Summary always reads the current snapshots. The HTTP reference lists the parameters of each route. The Ledger gets read-only listings of Snapshot Scopes and of the Allocation Snapshots of a scope.

The read model can also get a filter of Billing Lines (spec 09, Q82). The API does not set it. With a filter, each read sees only the lines that the filter keeps and the Allocations of them. This applies to the Cost Summary, the Allocations, the Roll-ups, the Cost Dimensions, the reconciliation and each Cost Explanation. An Allocation keeps its ref.

## Resources

### `GET /health`
Liveness, and whether the database opens with the current schema.

### `GET /v1/costs/summary`
One Cost Summary for each Snapshot Scope that matches, for one Cost Basis. It also lists the scopes that have no Allocation Snapshot yet.
- Each summary comes from the served Allocation Snapshot of that scope. It never mixes snapshots (#105).
- `cost_basis` selects the Cost Basis: one of the four FOCUS cost columns. The default is BilledCost.
- `billing_period` is the first day of the Billing Period.
- The billed total is the Export Snapshot that the Calculation Run read. The Ledger records it with each Allocation Snapshot.

The summary gives these amounts for each currency, and never sums them across currencies:
- the billed total
- the allocated amounts by Allocation Category and Confidence
- the Unallocated amounts by reason
- the Adjustments, as their own labeled amount (OQ-18)
- the Reconciliation Delta against the billed total
- the policy version and the Calculation Run

### `GET /v1/allocations`
One page of the Allocations of one Allocation Snapshot (#109).
- `calculation_run_id` names a superseded or current snapshot.
- If there is no `calculation_run_id`, the snapshot is the current one of the single Snapshot Scope that `billing_account_id`, `billing_period` and `source` select.
- If more than one scope matches, the answer is 400 with a request to narrow the filters. If no scope matches, the answer is 404.
- `calculation_run_id` together with scope filters gives 400, because a run sets its own scope.
- Filters: target, Allocation Category, Billing Line or Cost Basis.
- The address of each Allocation is `<calculation_run_id>#<position>`, as the address of a Billing Line is `<snapshot_id>#<position>`.

### `GET /v1/allocations/{ref}/explanation`
The Cost Explanation for one Allocation. It has these parts, in this order:
1. The Billing Line exactly as exported: each FOCUS column, the raw values of the provider, `Tags` and all provider columns (owner decision, #110).
   - By default, a Deployment shows a minimal Billing Line. The response states `billing_line_detail` (ADR 0028).
2. The Allocation: category, method, Confidence, Coverage, policy version and Calculation Run.
3. Its Evidence, with each reference resolved:
   - the Query Execution metadata
   - the Model Execution, with its dbt Node and status
   - the Workload Runs, nearest first, with their status. A run that is known only as the parent or root of a different run shows as none.
4. The Roll-ups that it contributes to, in the currency and Cost Basis of the Allocation.

If the Evidence names a Model Execution or a Query Execution that the store does not have, it shows as none.

### `GET /v1/rollups?level=model_execution|workload_run`
Roll-ups for each currency and Cost Basis. This route is separate from `/v1/allocations`, because a Roll-up is a view, never an Allocation.

### `GET /v1/rollups/dimensions/{name}`
What each value of one Cost Dimension holds in one Allocation Snapshot (spec 09, ADR 0031).
- The snapshot is selected as for `/v1/allocations`.
- Each value has its amounts for each currency and Cost Basis, and how its sources split.
- Unassigned cost has the value null.
- The page has `totals`: the totals of all values, by currency and Cost Basis. They equal the totals of the snapshot, so a reader can check the page and does not add the amounts.
- If no Allocation of the snapshot has the dimension, the answer is 404.

### `GET /v1/lineage`
Without parameters, the root Workload Runs, in pages. The workload store gets a read-only list of root runs.

With `run_id`, the route gives these items:
- that run and its status, parents, ancestors and children
- the Data Assets in and out, and the Lineage Edges
- its Identity Links with their Link Outcomes, reached only through `linked` links (#114)

For an invocation run, the links are these:
- the invocation and its outcome
- when the invocation links to this run, two more items:
  - its Model Executions. Each Model Execution comes with each query that claims it (`linked` or `ambiguous`).
  - the queries that name the invocation but a node that is not reported (`unmatched`)

For a dbt-ol model run, the route gives its link to its Model Execution.

`no_evidence` queries name no run, so they never show in lineage. The workload store lists the root runs.

### `GET /v1/reconciliation`
Allocations against the bill, for each Billing Line, Cost Basis and currency (#112).
- Each row gives the billed amount, the sum of Allocations and the Reconciliation Delta (expected zero).
- Totals for each currency cover all rows. The rows are in pages.
- The snapshot is selected as for `/v1/allocations`.
- Without `cost_basis`, each Cost Basis is shown, because each one reconciles on its own (OQ-17).
- An amount can be allocated to a line that is not in the bill, or in a currency different from the currency of its line. Then it is billed zero there. Thus invented amounts show as a negative delta.
- The Reconciliation of the Ledger against the export stays at import (M1).

### `GET /v1/provenance`
One Calculation Run:
- the input digest, and the policy and engine versions
- the Export Snapshot that it read (content digest, publication time)
- the creation time
- whether its Allocation Snapshot is current, and which snapshot superseded it

## Wire format
- Amounts are Decimal strings (`DecimalString`), never floats. Idle has no Confidence (OQ-20 stays open).
- Pagination is a bounded offset and limit. `limit` has a default of 100, and a maximum of 1000.
- Allocation Snapshots are immutable, so offsets are stable. Each page has `calculation_run_id`, `total`, `limit` and `offset`.
- The Workload Run list (`/v1/lineage`) reads the workload store, not a snapshot. Its pages have only `total`, `limit` and `offset`.
- Errors are RFC 9457 Problem Details (`application/problem+json`: type, title, status, detail).
- Input values are never shown in a response. Validation errors name only the field. No response contains SQL or secrets (acceptance.md).
- The OpenAPI 3.1 document that FastAPI generates is checked in as `schemas/openapi.json`. A drift test fails when it is stale, and contract tests validate responses against it.

## Dashboard
Static HTML, CSS and JavaScript that the API serves from the package. It has no build step, no JavaScript or chart libraries and no CDN, and it works offline. It reads only the JSON API.

The design follows Edward Tufte (*The Visual Display of Quantitative Information*) and Stephen Few (*Information Dashboard Design*, *Show Me the Numbers*):
- One screen answers the first question at a glance: where did the bill of this period go? The Cost Summary is the overview. Details come on demand, through the Cost Explanation.
- Maximize the data-ink. Use no gauges, dials, pie or donut charts, 3-D, gradients, shadows, decorative icons or heavy gridlines. Each mark encodes data.
- Use tables where exact amounts are important. Use right-aligned tabular figures at the scale of each line. State the currency one time in each column. Use light rules only between groups.
- To compare parts of the bill, use horizontal bars on a common zero baseline, sorted by amount. Label the bars directly, not through a legend. The parts are the amounts allocated by Confidence, Unallocated by reason, Adjustments and Roll-ups.
- By default, color is muted. Use it only to get attention: one highlight for amounts that require attention (Unallocated, a Reconciliation Delta that is not zero, failed runs). Never use color as decoration or as the only cue.
- Estimates look different from billed facts. Metered Shares show their Confidence and Coverage next to the amount (acceptance.md). Thus the reader never thinks that an estimate is an exact cost.
- Keep the placement and the terms consistent. Use only GLOSSARY terms (Unallocated, never "unknown"). The policy version, the Calculation Run and the Reconciliation Delta are always visible, but not prominent.
- Use small multiples when the same chart repeats across currencies or Cost Bases. Use shared scales in a currency, never across currencies.

## The server
The server has one uvicorn worker. The endpoints are synchronous, and they run in the thread pool of FastAPI.

The OpenLineage receiver is the exception. It reads its body as a stream, so a body over the limit is refused at the limit and not buffered (ADR 0024). Then it ingests the event in the thread pool (#107).

`reporting` opens its own read connections for each request, because the stores use `check_same_thread`. The receiver writes through the `BEGIN IMMEDIATE` of `WorkloadStore`, next to those readers (WAL, ADR 0018).

Only `data-finops calculate`, `demo` and a Collection Run (`collect`, or the collector of a Deployment) start a Calculation Run. Thus a received event changes no cost until the next Calculation Run. The Cost Summary and the provenance show when the served snapshot was calculated.

## Tests
There are three seams. Each is test-first, with hand-made mutation checks recorded in the PR:
1. The public interface of `reporting`, on a temporary SQLite file. The current public ingest functions and `run_calculation` fill it from the fixtures. Assertions are only on the returned models, never on tables. They cover the golden amounts, Roll-ups, a zero Reconciliation Delta, the Cost Explanation chain and the page limits. Most cases are here.
2. The HTTP app, through the test client of FastAPI, over the same database. The tests cover status codes and Problem Details, and the validation of filters and pages. They also cover the 201, 202, 400, 413 and 401 of the receiver, and the token and bind rules. Responses must be valid against `schemas/openapi.json`. No response contains SQL or the token. No business numbers are asserted here.
3. `WorkloadStore.ingest_event(bytes)`, on the current store seam. The test ingests the items of a fixture file one at a time. The counts and the stored runs are the same as from `ingest_events` on the file. The invalid fixtures are rejected with the same kinds.

The command line is tested through `main(argv)` on a temporary database, without a server. The dashboard has a smoke test only. It checks that its files are served, and that each API path that it calls is in the OpenAPI document. Its design is checked by eye in the exit walkthrough.

## Exit: the demo walkthrough
`examples/demo/README.md` is written again as `data-finops` commands, with what to see at each step. The M5 exit test runs each step through the command line and the test client:
- a $10.00 bill
- `q-001` with $6 metered, Coverage 1, rolled up to `model.demo.orders`
- `model.demo.customers` with no linked query (only an ambiguous claim)
- $4 Unallocated
- a second import of the same export changes nothing
- the restated export (`fixtures/focus/export-2026-10-restated.json`, added by #113) makes a new current Allocation Snapshot, and keeps the old one

A manual browser pass of the dashboard is recorded in progress.md, with one screenshot and its time against the five-minute budget (spec 01).
