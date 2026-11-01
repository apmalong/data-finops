# ADR 0017: A Metered Share requires measured Coverage

Status: Accepted (design, 2026-11-01). Refines ADR 0006; resolves the conflict between the demo walkthrough and the golden oracle.

## Context
Athena on-demand charges arrive as aggregated Billing Lines, not per query. Apportioning a line by each known query's billable usage is the product's core value, but it overstates known queries whenever query history is incomplete (limited API retention, workgroups shared with non-dbt users). The package contradicted itself: the demo gave a $6 Athena line to the only known query's model, while the golden oracle kept it Unallocated.

## Decision
1. Coverage for a Billing Line = (sum of known Query Executions' Billable Usage) / (the line's billed usage quantity from FOCUS), computed in the line's own units after the provider's minimums and rounding.
2. Coverage = 1 (within the provider's rounding): the whole line is split among the known Query Executions by Billable Usage.
3. Coverage < 1: the covered fraction is split by Billable Usage; the remainder is Unallocated; Coverage is recorded on the Allocations.
4. Coverage > 1: the Evidence conflicts (wrong scope, free tier, or pricing-rule drift). The whole line is Unallocated and flagged.
5. A Metered Share is category `direct`, confidence `metered_estimate`. Confidence does not apply to Unallocated amounts.
6. Billable Usage rules (per-query minimums, rounding, chargeability of failed, canceled and DDL queries) are provider pricing in a versioned provider policy, never core logic.
7. Adjustment lines are Unallocated in the MVP and shown as their own labelled amount. Evidence-linked credit allocation is a later opt-in policy; net-of-discount showback uses the EffectiveCost view.

## Consequences
An operator never has to declare history complete; it is measured from standard FOCUS fields. Partial history still yields useful, honestly bounded Allocations. The golden oracle gives the $6 line to `q-001` as a Metered Share, on the stated assumption that the line's billed quantity equals `q-001`'s Billable Usage; the M0 fixture rebuild must encode that quantity in FOCUS form.

## Alternatives rejected
- Never meter without billing-level linkage: Athena never provides it, so nothing would ever be allocated.
- Always meter among known queries: silently inflates their cost when history is partial.
- Operator-declared completeness: unverifiable.

## Revisit triggers
The provider's billed quantity is unavailable or not comparable to query-level usage (*verify* for AWS FOCUS export); a provider exposes per-query billing.
