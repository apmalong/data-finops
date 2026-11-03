# HTTP API and dashboard (M5)

Decided in the M5 design session (2026-11-03). The `reporting` read model answers every read; `api` is a thin FastAPI adapter over it (architecture-boundaries.md). The OpenLineage receiver is in spec 04; ingress and token in ADR 0024.

## Which snapshot a read serves
Every read serves the current Allocation Snapshot of each Snapshot Scope unless told otherwise. Filters: `billing_account_id`, `billing_period`, and `calculation_run_id` to read a superseded snapshot. The Ledger gains read-only listings of Snapshot Scopes and of the Allocation Snapshots of a scope.

## Resources
- `GET /health`: liveness and whether the database opens with the current schema.
- `GET /v1/costs/summary`: one Cost Summary per matching Snapshot Scope (each from that scope's served Allocation Snapshot, never mixing snapshots; #105), plus the scopes with no Allocation Snapshot yet, for one Cost Basis (`cost_basis`, default BilledCost; any of the four FOCUS cost columns). `billing_period` is the Billing Period's first day. The billed total is the Export Snapshot the Calculation Run read, which the Ledger records with each Allocation Snapshot. Per currency, never summed across currencies: billed total; allocated amounts by Allocation Category and Confidence; Unallocated amounts by reason; Adjustments as their own labelled amount (OQ-18); Reconciliation Delta against billed; the policy version and Calculation Run.
- `GET /v1/allocations`: one page of one Allocation Snapshot's Allocations (#109): a superseded or current one named by `calculation_run_id`, or else the current one of the single Snapshot Scope that `billing_account_id`, `billing_period` and `source` select. Several matching scopes answer 400 asking to narrow; none answers 404; `calculation_run_id` with scope filters answers 400, since a run fixes its own scope. Filtered by target, Allocation Category, Billing Line or Cost Basis. Each Allocation is addressed as `<calculation_run_id>#<position>`, like a Billing Line's `<snapshot_id>#<position>`.
- `GET /v1/allocations/{ref}/explanation`: the Cost Explanation for one Allocation, in order: the Billing Line (FOCUS columns and raw provider values); the Allocation (category, method, Confidence, Coverage, policy version, Calculation Run); its Evidence with each reference resolved (Query Execution metadata, Model Execution with its dbt Node and status, Workload Runs nearest first with status); and the Roll-ups it contributes to.
- `GET /v1/rollups?level=model_execution|workload_run`: Roll-ups per currency and Cost Basis. Kept apart from `/v1/allocations` because a Roll-up is a view, never an Allocation.
- `GET /v1/lineage`: without parameters, the root Workload Runs (paginated; the workload store gains a read-only listing of root runs). With `run_id`, that run and its status, parents, ancestors and children, Data Assets in and out, Lineage Edges, and its Identity Links with Link Outcomes (Model Execution; Query Executions, including `no_evidence`, `unmatched` and `ambiguous`).
- `GET /v1/reconciliation`: Allocations against the bill, per Billing Line and Cost Basis: billed amount, sum of Allocations and Reconciliation Delta (expected zero), with per-currency totals. The Ledger-against-export Reconciliation stays at import (M1).
- `GET /v1/provenance`: one Calculation Run: input digest, policy and engine versions, the Export Snapshot read (content digest, publication time), creation time, whether its Allocation Snapshot is current and which one superseded it.

## Wire format
- Amounts are Decimal strings (`DecimalString`), never floats. Idle carries no Confidence (OQ-20 stays open).
- Pagination is bounded offset and limit: `limit` defaults to 100, at most 1000. Allocation Snapshots are immutable, so offsets are stable; each page carries `calculation_run_id`, `total`, `limit` and `offset`.
- Errors are RFC 9457 Problem Details (`application/problem+json`: type, title, status, detail). Input values are never echoed; validation errors name the field only. No SQL or secrets in any response (acceptance.md).
- FastAPI's generated OpenAPI 3.1 document is checked in as `schemas/openapi.json`; a drift test fails when it is stale, and contract tests validate responses against it.

## Dashboard
Static HTML, CSS and JavaScript served by the API from the package: no build step, no JavaScript or chart libraries, no CDN, works offline. It reads only the JSON API.

Design follows Edward Tufte (*The Visual Display of Quantitative Information*) and Stephen Few (*Information Dashboard Design*, *Show Me the Numbers*):
- One screen answers the first question at a glance: where did this period's bill go. The Cost Summary is the overview; details come on demand through the Cost Explanation.
- Maximise data-ink: no gauges, dials, pie or donut charts, 3-D, gradients, shadows, decorative icons or heavy gridlines. Every mark encodes data.
- Tables where exact amounts matter: right-aligned tabular figures at the line's own scale, the currency stated once per column, light rules only between groups.
- Horizontal bars on a common zero baseline, sorted by amount, labelled directly instead of through a legend, for comparing parts of the bill (allocated by Confidence, Unallocated by reason, Adjustments, Roll-ups).
- Colour is muted by default and used only to draw attention: one highlight for amounts that need it (Unallocated, a non-zero Reconciliation Delta, failed runs), never as decoration and never as the only cue.
- Estimates look different from billed facts: Metered Shares are labelled with their Confidence and Coverage beside the amount (acceptance.md), so the reader never mistakes an estimate for an exact cost.
- Consistent placement and terms: glossary terms only (Unallocated, never "unknown"); the policy version, Calculation Run and Reconciliation Delta are always visible, quietly.
- Small multiples when the same chart repeats across currencies or Cost Bases, with shared scales within a currency, never across currencies.

## Running the server
One uvicorn worker; endpoints are synchronous and run in FastAPI's thread pool. `reporting` opens its own read connections per request (the stores use `check_same_thread`); the receiver writes through `WorkloadStore`'s `BEGIN IMMEDIATE` beside those readers (WAL, ADR 0018). Only `data-finops calculate` and `demo` start a Calculation Run: a received event changes no cost until the next one, and the Cost Summary and provenance show when the served snapshot was calculated.

## Testing
Three seams, each test-first with hand-made mutation checks recorded in the PR:
1. `reporting`'s public interface on a temporary SQLite file filled through the existing public ingest functions and `run_calculation` from the fixtures. Assertions are on the returned models only (the golden amounts, Roll-ups, a zero Reconciliation Delta, the Cost Explanation chain, paging bounds), never on tables. Most cases live here.
2. The HTTP app through FastAPI's test client over the same database: status codes and Problem Details, filter and paging validation, the receiver's 201/202/400/413/401, the token and bind rules, responses valid against `schemas/openapi.json`, and no SQL or token in any response. No business numbers are asserted here.
3. `WorkloadStore.ingest_event(bytes)` on the existing store seam: ingesting a fixture file's items one at a time gives the same counts and stored runs as `ingest_events` on the file, and the invalid fixtures are rejected with the same kinds.

The command line is tested through `main(argv)` on a temporary database, without starting a server. The dashboard has a smoke test only (its files are served; every API path it calls is in the OpenAPI document); its design is checked by eye in the exit walkthrough.

## Exit: the demo walkthrough
`examples/demo/README.md` is rewritten as `data-finops` commands with what to see at each step. The M5 exit test runs every step through the command line and the test client: a $10.00 bill; `q-001` with $6 metered, Coverage 1, rolled up to `model.demo.orders`; `model.demo.customers` with no Evidence; $4 Unallocated; a re-import changes nothing; a restated export (a fixture the exit ticket adds) makes a new current Allocation Snapshot and keeps the old one. A manual browser pass of the dashboard, with one screenshot and its timing against the five-minute budget (spec 01), is recorded in progress.md.
