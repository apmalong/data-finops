# ADR 0016: Allocate to Query Executions; everything above is a Roll-up

Status: Accepted (design, 2026-11-01). Refines ADR 0007; narrows the mission in `README.md` for the MVP.

## Context
The mission lists queries, runs, pipelines, models, datasets, data products, tenants and downstream consumers as attribution targets. Allocating independently at several of these levels double-counts. Consumer attribution redistributes a producer's cost along lineage, which double-counts if shown beside direct cost. Spec 03 also left the reconciliation unit ambiguous between the Cost Pool and the Billing Line.

## Decision
1. Each Billing Line is partitioned exactly once. A Cost Pool only supplies the weights used to partition each of its lines.
2. In the MVP, the only Allocation Targets are Query Executions and the synthetic targets Idle and Unallocated.
3. Model Executions, Workload Runs, DAGs and models receive cost only as Roll-ups: sums of their linked Query Executions' Allocations. A Roll-up is a view, never a further Allocation.
4. Datasets, data products, tenants and downstream consumers are deferred. Each needs its own Evidence source and conservation rule, and must be presented as a separately reconciled view, never added to direct cost.
5. Charges for failed or retried work go to the Query Execution that incurred them, shown with its status. Whether a provider bills failed queries is provider pricing, held in the versioned provider policy.

## Consequences
Conservation is checked per Billing Line, which makes corrections line-scoped and keeps Adjustments tied to their own line. Allocation records reference the Billing Line they partition as well as the pool. The demo and acceptance scenarios present model and DAG cost as Roll-ups.

## Alternatives rejected
- Pool-level partition: hides misallocation between lines and forces whole-pool recomputation on a single correction.
- Independent allocation per level: double-counts.
- Charging failed or retried work to Unallocated: hides real waste the product should expose.

## Revisit triggers
A deferred target type gets an Evidence source and a conservation rule; a provider bills at a granularity coarser than a query in a way Roll-ups cannot express.
