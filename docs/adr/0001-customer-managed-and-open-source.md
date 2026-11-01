# ADR 0001: Customer-managed and open source

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Use customer-managed deployment and a free open-source core; Apache-2.0 is proposed for original project code pending contributor/license review. No mandatory SaaS control plane.

## Consequences
Commercial and hosted options may be considered separately but not required.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
