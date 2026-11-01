# ADR 0003: Authoritative billing ledger

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Cloud billing records are the financial source of truth. Telemetry allocates rather than creates charges. Preserve source cost basis, credits, currencies and revisions.

## Consequences
Allocations can be estimates while totals remain reconciled.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
