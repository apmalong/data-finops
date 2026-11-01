# ADR 0007: Deterministic cost allocation

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Use Decimal, stable rounding, versioned policies, and distinct direct/shared/idle/unallocated buckets.

## Consequences
More complexity but auditability and no double counting.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
