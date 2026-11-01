# ADR 0010: Read-only recommendations

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Optimization advice is advisory, with evidence and estimated savings; no automatic remediation.

## Consequences
Avoid introducing operational risk to production workloads.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.
