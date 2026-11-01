# ADR 0006: Explicit identity evidence

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Require exact provider query IDs or trusted correlation metadata for direct linkage. Time overlap alone cannot establish attribution.

## Consequences
Ambiguous spending stays unallocated.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
