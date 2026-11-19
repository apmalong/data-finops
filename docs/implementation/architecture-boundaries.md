# Architecture boundaries

`tests/unit/test_import_boundaries.py` enforces these rules with a static import scan. It also checks that this list names each package in `src/data_finops/` (#207).

- `contracts`: the Pydantic models that all other packages exchange (Billing Line, Export Snapshot, Allocation, workload records).
  - Through `schemas.py`, it also holds the generated JSON Schemas (code first, OQ-15).
  - It imports nothing else from `data_finops`.
- `billing`: provider billing readers and canonical billing facts.
  - the FOCUS reader (`billing.focus`). It reads with pyarrow. The only other module that uses pyarrow is `public_demo.dataset`.
  - the AWS Data Exports Delivery reader (`billing.aws_data_exports`)
- `ledger`: immutable versioned financial source records (Export Snapshots, Billing Lines) and Allocation Snapshots.
  - It also holds the reconciliation against a provider export (`ledger.reconciliation`), and the Delivery part of the Collection Watermark.
  - `Ledger.ingest_focus` reads a FOCUS file with the `billing` readers, then imports and reconciles it (#212). This is the one way that a FOCUS file is loaded.
  - It knows no workload Evidence and no layer above it.
- `identity`, `lineage`, `dbt`: workload and data asset Evidence, with no financial authority. `identity` holds pure link rules.
- `workload`: the workload store.
  - It stores Workload Runs, Model Executions, Data Assets, Lineage Edges, Query Executions, and the Inbox part of the Collection Watermark.
  - It is over the `lineage` and `dbt` readers, and the Athena query history reader in `providers.athena`. That reader only reads files, and makes no AWS calls.
  - It resolves Identity Links with the pure rules in `identity`.
  - It shares the database file of the Ledger, but it imports nothing from `billing`, `ledger` or `attribution` (ADR 0018 amendment, ADR 0022).
- `attribution`: deterministic pure calculations over immutable records. It has no network I/O, clock or cloud SDK imports.
  - It imports nothing from `providers`, `ledger` or `workload`. The callers classify Billing Lines into pools, and they give the Billable Usage (`attribution.engine`, #81).
  - Roll-ups (`attribution.rollups`) are pure sums too. So are Cost Dimension values and totals (`attribution.dimensions`, ADR 0031).
- `calculation`: the Calculation Run (#88). It is a thin orchestrator over `ledger`, `workload`, `providers` and the pure `attribution` engine.
  - All four meet only here. `collection` (below) gets to them through `calculation`, and gives it what it collected.
  - It resolves the Evidence of each Allocation with `attribution.rollups`.
  - No SQL and no AWS.
- `reporting`: the read model (M5). It is read-only.
  - It answers cost summaries, allocations, explanations, lineage, reconciliation and provenance from the `ledger` and `workload` read interfaces. It returns response models.
  - It computes Roll-ups and Cost Dimension totals at read time with the pure `attribution.rollups` and `attribution.dimensions`.
  - Only `reporting`, `calculation` and `collection` import both stores. `cli` can too, with the condition in its entry below.
- `api`: the HTTP adapter (FastAPI, optional `api` extra) and the static dashboard.
  - It reads only through `reporting`. Its one write gives OpenLineage events to `WorkloadStore` (ADR 0024).
  - No SQL.
- `cli`: the `data-finops` command line (stdlib `argparse`): `demo`, `serve`, `ingest`, `calculate`, `collect`, and the reads of spec 10 (`summary`, `allocations`, `rollups`, `reconciliation`, `provenance`, `lineage`, `explain`).
  - A read against `--db` calls `reporting`. A read against `--deployment` sends a GET to the API of the Deployment, signed by `aws_clients` (`cli/deployments.py`). Both give the JSON of the HTTP route.
  - It is an entry point. It opens the stores and calls their public functions, and it joins nothing itself.
  - It can import both stores only together with `calculation`, to give them to the Calculation Run (#115). The import-boundary test enforces this.
  - For a FOCUS file, it calls `Ledger.ingest_focus`, which reads the file with the `billing` readers, imports it and reconciles it (#212). A Collection Run calls it too.
- `providers`: optional cloud-specific collectors and provider pricing policies (`providers.athena_billing`, #82). The attribution core must not import it.
  - `providers.aws` (M6, spec 08) holds the AWS collectors, behind the optional `aws` extra (boto3).
  - These collectors fetch files and API responses, and give them to the current readers. They never write to a store themselves.
- `collection`: the Collection Run (M6, spec 08). It is a thin orchestrator over `providers.aws`, the `billing` Delivery layout, the public ingest functions of the stores, and `calculation`. It loads each Delivery through `Ledger.ingest_focus` (#212).
  - `collection.remote` fetches and writes the database object.
  - The Lambda handlers (`lambda_handlers`) are entry points over `collection`, `api` and `public_demo`, as `cli` is. The API handler and the publisher keep a copy of the database object through `collection.remote.CachedDatabase`, which gets it again only when its ETag changes. The handlers do not import `providers` (#214).
  - The entry points (`cli` and `lambda_handlers`) get their AWS clients from `aws_clients`, the only module that imports boto3 (#183, #184).
- `public_demo`: the parts of the public demo that run in AWS (M7, spec 09). It is not the `data-finops demo` command.
  - `public_demo.dataset` makes the demo dataset as Parquet, and writes it through `providers.aws`. It is the second module that can import pyarrow (ADR 0018, amended).
  - `public_demo.simulator` is the simulated orchestrator. It runs the plan of a start in Athena, through `providers.aws`, and writes the records to the Inbox. `public_demo.workload` holds its DAGs and models as data.
  - `public_demo.publisher` renders the Demo Snapshot through the public interface of `reporting`, with a filter of Billing Lines, and writes it through `providers.aws`.
  - The `generator`, `simulator` and `publisher` Lambda handlers are its entry points. No other package imports `public_demo`.

Do not enforce a simplistic linear import graph. Identity and lineage are independent inputs to attribution, not submodules of billing.
