# ADR 0018: SQLite ledger store, and pyarrow for Parquet

Status: Accepted (2026-11-01). Narrows ADR 0009. Decides OQ-4 for the MVP. Amended 2026-11-01 (M2): the workload store shares the file.

## Context
M1 requires a persistent local store for the Ledger and a reader for provider Parquet exports (AWS Data Exports writes FOCUS as Parquet). ADR 0009 left the choice between DuckDB and SQLite open, and OQ-4 asked for a benchmark first.

## Decision
1. The Ledger persists to SQLite (Python standard library), behind the Ledger's public interface. No other module touches the database.
2. Parquet exports are read with pyarrow, inside the FOCUS export reader only.
3. DuckDB is rejected for the MVP store: it allows a single writing process per database file, which conflicts with a collector, a recalculation and the API working against the same store. SQLite in WAL mode allows concurrent readers alongside one writer across processes.

## Consequences
Two components instead of one, and pyarrow is a large wheel. It is only in the reader, so the core does not depend on it. Operator tools in `scripts/` (for example the FOCUS export anonymizer, #18) can also use it. They are not part of the package.

Analytical queries run in Python or SQL over SQLite. If that is too slow at the target scale, the store can change behind the Ledger interface, and the callers do not change. Decimal amounts are stored as text to keep exact values and scale (SQLite has no exact decimal type).

## Alternatives rejected
- DuckDB for both store and Parquet: one dependency, but its single-writer process model conflicts with concurrent components.
- SQLite plus DuckDB for analytics: two stores to keep consistent.

## Revisit triggers
- Measured query latency at the target scale.
- A deployment that requires more than one concurrent writer. Then consider Postgres, which spec 01 already calls "optional later".

## Amendment (2026-11-01, M2)
Workload metadata (Workload Runs, Model Executions, dbt manifest nodes, Data Assets and Lineage Edges) is stored in the same SQLite database file as the Ledger. A separate workload store module owns its tables. Billing code and lineage code do not import each other (architecture boundaries). They share only the file, WAL mode and `BEGIN IMMEDIATE` writes.
