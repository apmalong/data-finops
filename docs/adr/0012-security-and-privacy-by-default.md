# ADR 0012: Security and privacy by default

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Private ingestion, least privilege, metadata minimization, bounded payloads, no exported SQL literals or credentials.

## Consequences
May reduce observability granularity unless opt-in.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
