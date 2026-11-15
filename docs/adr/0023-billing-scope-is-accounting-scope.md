# ADR 0023: A Billing Line's scope is accounting scope, not Evidence

Status: Accepted (design, 2026-11-02). Clarifies ADR 0006 for ADR 0017's Coverage.

## Context
Coverage (ADR 0017) compares a Billing Line's billed usage quantity with the Billable Usage of the known Query Executions behind it. Which Query Executions are "behind" an aggregated Athena line? To decide, data-finops compares the account, Region, workgroup and time of the query with the values of the line. ADR 0006 forbids time as Evidence, so the two rules looked contradictory.

## Decision
1. A Query Execution falls in a Billing Line's scope when it matches the line's billing dimensions: account, region, the workgroup if the provider bills by workgroup, and the charge period containing the query's completion. This is the scope the provider itself billed, so it is accounting scope, not identity Evidence.
2. Scope decides only which Query Executions count toward Coverage and share the line by Billable Usage. It never creates an Identity Link and never attributes a Query Execution to a Model Execution or Workload Run; those still need Evidence (ADR 0006, M3).
3. The exact keys (which FOCUS column carries the workgroup, whether rows are split per workgroup, which query timestamp the provider bills by) are provider policy, decided from a real export (#25), not core logic.

## Consequences
Query Executions without a link (Link Outcome `no_evidence`, `unmatched`, `ambiguous`) still count toward Coverage, and they still get Metered Shares. The only difference: they roll up to no Model Execution (ADR 0016). A wrong scope key shows as Coverage above one, which ADR 0017 already makes Unallocated, with a flag.

## Alternatives rejected
- Count only linked Query Executions: Coverage would understate known usage and push linked cost into Unallocated whenever any query lacks a comment.
- Treat a scope match as Evidence: then time and workgroup can make attribution, which ADR 0006 forbids.

## Revisit triggers
A provider bills at a scope finer than a query (no aggregation), or the export lacks a dimension that is required to match scope.

## Note (drift review #207, 2026-11-07)
The "charge period containing the query's completion" of point 1 is the choice of timestamp of `athena-billing-scope`, a provider policy (point 3). The real export billed two queries in the line of the next hour, but they completed seconds before that hour (OQ-25, open). Thus the completion is not always the timestamp that AWS bills by. The revisit trigger "the export lacks a dimension that is required to match scope" has partly fired. OQ-25 decides if the policy changes.
