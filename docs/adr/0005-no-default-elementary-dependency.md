# ADR 0005: No default Elementary dependency

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Parse standard dbt artifacts directly. Support Elementary warehouse tables as optional adapter.

## Consequences
Reduced customer setup, possible narrower initial metadata.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
