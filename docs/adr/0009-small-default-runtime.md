# ADR 0009: Small default runtime

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Use local DuckDB/SQLite and FastAPI/static HTML for MVP. Optional managed persistence later.

## Consequences
Avoid mandatory Kubernetes, Postgres, Marquez, OpenCost.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
