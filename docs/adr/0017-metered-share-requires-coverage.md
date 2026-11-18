# ADR 0017: A Metered Share requires measured Coverage

Status: Accepted (design, 2026-11-01). Refines ADR 0006. Resolves the conflict between the demo walkthrough and the golden oracle.

## Context
Athena on-demand charges arrive as aggregated Billing Lines, not one line for each query. To apportion a line by the billable usage of each known query is the core value of the product. But it overstates the known queries when the query history is not complete (limited API retention, workgroups shared with users who do not use dbt). The package contradicted itself. The demo gave a $6 Athena line to the model of the only known query, but the golden oracle kept it Unallocated.

## Decision
1. Coverage for a Billing Line = (sum of known Query Executions' Billable Usage) / (the line's billed usage quantity from FOCUS), computed in the line's own units after the provider's minimums and rounding.
2. Coverage = 1 (within the provider's rounding): the whole line is split among the known Query Executions by Billable Usage.
3. Coverage < 1: the covered fraction is split by Billable Usage; the remainder is Unallocated; Coverage is recorded on the Allocations.
4. Coverage > 1: the Evidence conflicts (wrong scope, free tier, or pricing-rule drift). The whole line is Unallocated and flagged.
5. A Metered Share is category `direct`, confidence `metered_estimate`. Confidence does not apply to Unallocated amounts.
6. Billable Usage rules (per-query minimums, rounding, chargeability of failed, canceled and DDL queries) are provider pricing in a versioned provider policy, never core logic.
7. Adjustment lines are Unallocated in the MVP and shown as their own labelled amount. Evidence-linked credit allocation is a later opt-in policy; net-of-discount showback uses the EffectiveCost view.

## Consequences
An operator never has to declare that the history is complete. The history is measured from standard FOCUS fields. Partial history still gives useful Allocations with honest limits. The golden oracle gives the $6 line to `q-001` as a Metered Share. It states the assumption that the billed quantity of the line equals the Billable Usage of `q-001`. The M0 fixture rebuild must encode that quantity in FOCUS form.

## Amendment (2026-11-18, #310)
The units of Athena's billed quantity were an assumption, not a fact. The owner's demo lines of 2026-11 came out at Coverage 1.11 in decimal units, so each line was Unallocated as `coverage_exceeded`. AWS does not state the units. `athena-billable-usage/3` reads `TB` as 2^40 bytes, with an hourly rounding fitted to the owner's lines (spec 03, item 8). The Coverage tolerance does not change (Q166). Decisions Q165 to Q168 are in `docs/implementation/m7-decisions.md`.

This amendment changes two statements above:
- Consequences: under `/3`, the golden fixture's `q-001` has a Billable Usage of 0.000905 TB, not 0.001 TB. The line exports 0.001 TB with three decimal places, so the difference is in the tolerance of decision 2. Coverage is still 1, and `q-001` still gets the $6 line.
- Revisit triggers: "in decimal TB" was an assumption (#310). A new trigger applies: real Athena lines are `coverage_exceeded`, or have a `coverage_remainder`, under `/3`. Then fit the policy again, or ask AWS Support.

## Alternatives rejected
- Never meter without billing-level linkage: Athena never provides it, so nothing would ever be allocated.
- Always meter among known queries: silently inflates their cost when history is partial.
- Operator-declared completeness: unverifiable.

## Revisit triggers
- The billed quantity of the provider is not available, or it cannot be compared to query-level usage. This was checked for the AWS FOCUS export: ConsumedQuantity equals PricingQuantity, in decimal TB, and real lines get to Coverage 1 (OQ-3, #91).
- A provider shows billing for each query.
