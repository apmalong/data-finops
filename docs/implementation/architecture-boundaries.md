# Architecture boundaries

- `billing` and `normalization`: provider data adapters and canonical billing facts.
- `ledger`: immutable versioned financial source records and reconciled pools.
- `identity`, `lineage`, `dbt`: workload and data asset evidence, no financial authority.
- `attribution`: deterministic pure calculations over immutable records, no network I/O, clock or cloud SDK imports.
- `api`: read-only presentation and validated query interfaces.
- `providers`: optional cloud-specific collectors; must not be imported by attribution core.

Do not enforce a simplistic linear import graph: identity and lineage are independent inputs to attribution, not submodules of billing. Add an import-boundary test during M0.
