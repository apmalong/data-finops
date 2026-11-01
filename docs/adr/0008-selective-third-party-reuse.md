# ADR 0008: Selective third-party reuse

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Prefer libraries/protocols. Copy minimal source only after pinned SHA, file-level license, dependency, tests and provenance audit.

## Consequences
More up-front diligence, less inherited architecture.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
