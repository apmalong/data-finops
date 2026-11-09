# ADR 0031: Cost Dimensions are organization-configured claims, recorded on Allocations

Status: Accepted (design, 2026-11-09). Decides M7's attribution configuration (Q70, Q71). Applies OQ-11's "tenants … deferred as separately reconciled views"; keeps ADR 0006 and ADR 0016.

## Context
Which attributes matter is different in every organization: team, tenant, cost center, DAG run. The practice is well established. Query tags on Snowflake, job labels on BigQuery and query comments on Athena carry those attributes from dbt and the orchestrator. No tool found takes them from the orchestrator through the query to the bill and reconciles the result; each covers a part (`docs/research/m7-attribution-precedents.md`). data-finops reads only `node_id` and `invocation_id` from a comment today and drops the query text before storage, so anything else in the comment is lost at ingest.

## Decision
1. **Configured dimensions.** An organization declares its Cost Dimensions in one TOML file. Each names its sources, in order; the first that gives a value wins, and the value records which source gave it.
2. **Views, never Allocation Targets.** Allocations are still made to Query Executions, Idle and Unallocated. Dimensions group them, as Roll-ups do, so every Billing Line is still partitioned exactly and each dimension's total equals the snapshot's total.
3. **Claims, not Evidence.** A value only says what something wrote. It is never Evidence for an Identity Link (ADR 0006), and the source is shown beside the number.
4. **Recorded on the Allocation.** A Calculation Run resolves each dimension and records the value and source on the Allocation. A stored snapshot therefore groups the same way whenever it is read (as ADR 0016's Roll-ups do).
5. **Changes apply going forward (Q72).** A changed configuration never rewrites a stored snapshot and is not, by itself, a reason to calculate: its digest is recorded on the snapshot but is not part of the input digest. It takes effect at the next snapshot that new inputs make.
6. **Unassigned stays visible.** Cost with no value for a dimension is reported as `unassigned`, beside the assigned values, never spread among them.
7. **Only configured keys are kept.** Ingest keeps the comment keys the configuration names, capped in number and length (metadata minimization, ADR 0012).
8. **Sensitive dimensions.** A dimension marked `sensitive` is left out of the minimal Cost Explanation and out of the Demo Snapshot.

## Consequences
- **No retroactive updates (Q72).** Query text is not kept, so a key added later cannot be read from queries already stored. A closed period keeps the dimensions it was calculated with. A period that receives new inputs, such as a later Delivery of the bill, is calculated again with the configuration of that moment.
- **The M7 sources are** a first-line query comment, OpenLineage tags on the linked Workload Run, and a workgroup map. Trailing comments (dbt on Snowflake, SQLCommenter) wait for a second engine.
- **The Allocation contract gains a field,** and the schemas and OpenAPI document change with it.

## Alternatives rejected
- **New Allocation Targets for teams and tenants:** a second partition of the same money, with Allocation Targets that differ per organization.
- **Keeping every comment key:** arbitrary user data in the store, against ADR 0012.
- **Resolving at read time:** a changed configuration would silently rewrite stored snapshots.
- **A rules engine with percentages (virtual tags):** more than M7 needs. Splitting shared cost is a separate decision.

## Revisit triggers
- A second query engine needs trailing comments or job labels.
- Organizations need to split one query's cost across several values.
- Sensitive values must be hashed rather than withheld.
