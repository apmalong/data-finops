# Architecture boundaries

- `billing` and `normalization`: provider data adapters and canonical billing facts.
- `ledger`: immutable versioned financial source records and reconciled pools.
- `identity`, `lineage`, `dbt`: workload and data asset evidence, no financial authority.
- `workload`: the workload store (Workload Runs, Model Executions, Data Assets, Lineage Edges) over `lineage` and `dbt` readers and the Athena query history reader in `providers.athena` (file reading only, no AWS calls), resolving Identity Links with the pure rules in `identity`; shares the Ledger's database file but imports nothing from `billing`, `ledger` or `attribution` (ADR 0018 amendment, ADR 0022).
- `attribution`: deterministic pure calculations over immutable records, no network I/O, clock or cloud SDK imports. It imports nothing from `providers`, `ledger` or `workload`: callers classify Billing Lines into pools and supply Billable Usage (`attribution.engine`, #81).
- `api`: read-only presentation and validated query interfaces.
- `providers`: optional cloud-specific collectors; must not be imported by attribution core.

Do not enforce a simplistic linear import graph: identity and lineage are independent inputs to attribution, not submodules of billing. Add an import-boundary test during M0.
