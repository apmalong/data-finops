# Offline demo walkthrough

You load a small synthetic bill and its workload metadata, then follow $10.00 of cost to the query, dbt model and Airflow run that used it. You need no cloud account. The walkthrough takes about five minutes.

`tests/integration/test_m5_exit.py` checks every command, printed line and amount below, as the API that draws the dashboard answers them, so a step that stops matching fails CI. How the dashboard looks is checked by eye.

**The fixtures are test inputs, not real AWS billing records or provider prices.**

## 0. Install

You need a source checkout (the demo reads its `fixtures/`), Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/apmalong/data-finops.git
cd data-finops
uv sync --extra api --frozen
```

## The quick way

```sh
uv run data-finops demo
```

This loads every fixture into a temporary database, calculates, and serves the dashboard at <http://127.0.0.1:8000/> until you press Ctrl+C. Then the database is deleted. Skip to step 3 to see what to look for.

## The long way, one step at a time

### 1. Ingest the bill and the workload

```sh
uv run data-finops ingest focus fixtures/focus/export-2026-10.json --db demo.sqlite
uv run data-finops ingest athena fixtures/athena/query-executions.json --account-id 123456789012 --region us-east-1 --db demo.sqlite
uv run data-finops ingest athena fixtures/athena/link-outcomes.json --account-id 123456789012 --region us-east-1 --db demo.sqlite
uv run data-finops ingest athena fixtures/athena/link-outcomes-restated.json --account-id 123456789012 --region us-east-1 --db demo.sqlite
uv run data-finops ingest openlineage fixtures/openlineage/events.json --db demo.sqlite
uv run data-finops ingest dbt fixtures/dbt/manifest.json --db demo.sqlite
uv run data-finops ingest dbt fixtures/dbt/run_results.json --db demo.sqlite
```

Each command prints what it stored, starting with `focus: new aws-focus-123456789012-2026-10-v1`.

The inputs are:
- one FOCUS Export Snapshot with two Billing Lines: $6.00 of Athena and $4.00 of S3;
- the Athena query history;
- the Airflow and dbt OpenLineage events;
- the dbt artifacts.

### 2. Calculate

```sh
uv run data-finops calculate --db demo.sqlite
```

This prints `calculate: 123456789012 2026-10-01 aws-data-exports:FOCUS_1_2_AWS: new calc-…`, one line per Snapshot Scope. The Calculation Run partitions every Billing Line into Allocations and stores them as the current Allocation Snapshot.

### 3. See where the bill went

```sh
uv run data-finops serve --db demo.sqlite
```

Open <http://127.0.0.1:8000/>. The page leads with the Cost Summary:
- the BilledCost total in USD, **10.00**, with a Reconciliation Delta of **0.00**;
- under the title, the Calculation Run, its time and its policy version.

The same answer comes from the API at <http://127.0.0.1:8000/v1/costs/summary>.

### 4. `q-001`: $6.00, a metered estimate

- **Direct**, metered estimate, Coverage 1: **6.00**. It's an estimate, so it is labelled with its Confidence and Coverage.
- Click it: its one Allocation opens its Cost Explanation, in this order:
  - the Billing Line (the $6.00 Athena line);
  - the Allocation to Query Execution `q-001`;
  - the Evidence, nearest first: `q-001`, Model Execution `model.demo.orders`, the dbt run, the Airflow task and the DAG run;
  - the Roll-ups.
- Under **What models and runs cost**, `model.demo.orders` has $6.00, and so do the dbt run and the Airflow runs above it. Roll-ups are sums of Allocations, never further Allocations.

Why Coverage 1: the Athena line bills exactly the Billable Usage of the queries known in its billing scope, so `q-001` gets the whole line by its bytes (ADR 0017).

### 5. `model.demo.customers`: no linked query

- Under **Workload Runs**, open `00000000-0000-4000-8000-000000000002`, the dbt run `dbt-run-demo`. Its lineage shows the runs above it, and its Identity Links show:
  - `model.demo.orders` claimed by its queries, linked;
  - `model.demo.customers` claimed only by `q-004`, whose reports disagree, so the Link Outcome is `ambiguous`.
- `model.demo.customers` has no Roll-up. Without an Identity Link, nothing is invented for it.

### 6. The $4.00 S3 line is Unallocated

**Unallocated**, ineligible: **4.00**, in the highlight colour. No MVP policy allocates S3 storage, so the amount stays Unallocated, named with its reason, and is never spread.

### 7. Running everything again changes nothing

Run steps 1 and 2 again:
- the Billing ingest prints `focus: replay …`;
- the calculation prints `replay calc-…` with the same Calculation Run;
- the dashboard shows the same $10.00.

### 8. A restated export

```sh
uv run data-finops ingest focus fixtures/focus/export-2026-10-restated.json --db demo.sqlite
uv run data-finops calculate --db demo.sqlite
```

The provider republished October, now with $5.00 of S3, as `aws-focus-123456789012-2026-10-v2`:
- the bill is now **$11.00**;
- the new Calculation Run is current;
- the previous one is kept. <http://127.0.0.1:8000/v1/provenance?calculation_run_id=calc-…> (the earlier run's ID) shows `"is_current": false` and `superseded_by`, which names the new run.

## Your own AWS bill

`data-finops ingest focus` also takes a downloaded AWS Data Exports Delivery (FOCUS 1.2) by its `<export>-Manifest.json`. Add `--data-dir` if its data files were saved elsewhere. Fetching from S3 is not part of the product yet (M6).
