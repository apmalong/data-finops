# ADR 0006: Explicit identity evidence

Status: Accepted (design). Amended 2026-11-16 by ADR 0032 (below).

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Require exact provider query IDs or trusted correlation metadata for direct linkage. Time overlap alone cannot establish attribution.

## Consequences
Ambiguous spend stays unallocated.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a new ADR that supersedes this one.

## Amendment (ADR 0032, 2026-11-16)
An applied link Override is now a kind of Evidence (Q155–Q160, `docs/implementation/m8-decisions.md`).
- A person with the consumer or operator Control Role can record that a Query Execution belongs to a Model Execution or a Workload Run.
- The link has the Link Outcome `override`. Its Evidence names the Override id and the principal that created it.
- It replaces any other link of that Query Execution for the Billing Periods that the Override reaches. This is also true for a link that a Trusted Query Comment makes.
- The Allocation keeps the Confidence of its method. The Evidence shows that a person, not the provider or dbt, made the link.
- Time overlap alone is still never Evidence. Without an Override, ambiguous spend stays unallocated.