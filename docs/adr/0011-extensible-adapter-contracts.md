# ADR 0011: Extensible adapter contracts

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Cloud, transformation, orchestration and BI integrations implement small capability-declared ports.

## Consequences
Provider-specific billing semantics remain isolated.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
