# ADR 0002: Python core and Pulumi Python

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Implement typed Python 3.12+ core and Pulumi Python infrastructure. Keep attribution engine runnable without Pulumi or cloud.

## Consequences
No cloud deployment required for offline tests.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
