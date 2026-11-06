# Architecture boundaries

- `billing` and `normalization`: provider data adapters and canonical billing facts.
- `ledger`: immutable versioned financial source records and reconciled pools.
- `identity`, `lineage`, `dbt`: workload and data asset evidence, no financial authority.
- `workload`: the workload store (Workload Runs, Model Executions, Data Assets, Lineage Edges) over `lineage` and `dbt` readers and the Athena query history reader in `providers.athena` (file reading only, no AWS calls), resolving Identity Links with the pure rules in `identity`; shares the Ledger's database file but imports nothing from `billing`, `ledger` or `attribution` (ADR 0018 amendment, ADR 0022).
- `attribution`: deterministic pure calculations over immutable records, no network I/O, clock or cloud SDK imports. It imports nothing from `providers`, `ledger` or `workload`: callers classify Billing Lines into pools and supply Billable Usage (`attribution.engine`, #81).
- `calculation`: the Calculation Run (#88), a thin orchestrator over `ledger`, `workload`, `providers` and the pure `attribution` engine; with `collection` (below), which hands it what it collected, the only place they meet.
- `reporting`: the read model (M5). Answers cost summaries, allocations, explanations, lineage, reconciliation and provenance from the `ledger` and `workload` read interfaces, returning response models; read-only. With `calculation` and `collection`, the only modules that import both stores.
- `api`: HTTP adapter (FastAPI, optional `api` extra) and static dashboard. Reads only through `reporting`; its one write is passing OpenLineage events to `WorkloadStore` (ADR 0024). No SQL.
- `cli`: the `data-finops` command line (stdlib `argparse`): `demo`, `serve`, `ingest`, `calculate`, `collect`. An entry point: it opens the stores and calls their public functions, joining nothing itself; it may import both only alongside `calculation`, to hand them to the Calculation Run (#115, enforced by the import-boundary test).
- `providers`: optional cloud-specific collectors and provider pricing policies (`providers.athena_billing`, #82); must not be imported by attribution core. `providers.aws` (M6, spec 08) holds the AWS collectors behind the optional `aws` extra (boto3); they fetch files and API responses and hand them to the existing readers, never writing a store themselves.
- `collection`: the Collection Run (M6, spec 08), a thin orchestrator over `providers.aws`, the stores' public ingest functions and `calculation`. Lambda handlers are thin entry points over `collection` and `api`, like `cli`.

Do not enforce a simplistic linear import graph: identity and lineage are independent inputs to attribution, not submodules of billing. Add an import-boundary test during M0.
