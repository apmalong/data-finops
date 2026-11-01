# Data FinOps

Attributes authoritative cloud charges to the data workloads that caused them, without creating, losing or double-counting money. Terms here are settled; contested terms are tracked in `docs/implementation/open-questions.md` until resolved.

## Billing

**Billing Line**:
One FOCUS row from a provider's billing export: an authoritative charge in one billing currency for one charge period, carrying each FOCUS cost column. It is the unit that Reconciliation partitions exactly once.
_Avoid_: bill row, invoice line, cost record

**Export Snapshot**:
The complete set of Billing Lines a provider published for one billing account, Billing Period and source at one point in time. A restatement replaces the whole snapshot; earlier snapshots are retained.
_Avoid_: file, batch, version (alone)

**Snapshot Scope**:
The billing account, Billing Period and source an Export Snapshot covers. At most one Export Snapshot is current for a Snapshot Scope.
_Avoid_: partition, key (alone)

**Billing Period**:
The provider's invoicing period that a Billing Line belongs to. It may differ from the line's charge period.
_Avoid_: month (alone)

**Cost Basis**:
Which FOCUS cost column a figure is taken from (for example BilledCost or EffectiveCost). Amounts in different cost bases are never added together.
_Avoid_: cost type, price

**Adjustment**:
A Billing Line whose charge category is not usage, such as a credit, refund or discount. Adjustments are their own lines, signed, and may be negative.
_Avoid_: rebate (unless the provider uses that term), negative cost

**Cost Pool**:
A set of eligible Billing Lines sharing one currency, cost basis, time window and pricing category, whose workloads supply the weights. Each Billing Line in the pool is partitioned by those weights; the pool itself is not the partitioned amount.
_Avoid_: bucket, cost group

**Eligibility**:
Whether a Billing Line may be allocated to workloads by an existing allocator. Ineligible lines remain Unallocated.

## Workload identity

**Query Execution**:
One provider-recorded execution of a query, identified by the provider's query execution ID within an account and region.
_Avoid_: query (alone), job

**Model Execution**:
One run of a dbt node within one dbt invocation. A dbt node ID alone never identifies an execution.
_Avoid_: model run, dbt job

**Workload Run**:
One orchestrated execution reported by an orchestrator or lineage producer, such as an Airflow task run, possibly with a parent run.
_Avoid_: job, task (alone), pipeline run

**Identity Link**:
A recorded assertion that two workload identities (for example a Query Execution and a Model Execution) are the same unit of work, together with the Evidence for it.
_Avoid_: match, correlation (alone), join

**Evidence**:
The explicit identifier or trusted metadata that justifies an Identity Link or an Allocation. Time overlap alone is never Evidence.
_Avoid_: signal, heuristic

## Lineage

**Data Asset**:
A dataset or other data object that workloads read or write, identified within a namespace.
_Avoid_: table (alone), entity

**Lineage Edge**:
An observed read or write relationship between a workload and a Data Asset, or between two Data Assets. Lineage has no financial authority.
_Avoid_: dependency (alone)

## Allocation

**Allocation**:
The portion of a Cost Pool assigned to one Allocation Target, with its method, Evidence and policy version.
_Avoid_: charge, cost (alone)

**Allocation Target**:
What an Allocation is assigned to: a Query Execution, or one of the synthetic targets Idle or Unallocated. Model Executions and Workload Runs receive cost only as Roll-ups.
_Avoid_: owner, cost center, tenant, data product, consumer (not targets in the MVP)

**Roll-up**:
The sum of Allocations over the Query Executions linked to a Model Execution, Workload Run or higher grouping. A Roll-up is a view, never a further Allocation.
_Avoid_: re-allocation, inherited cost

**Allocation Category**:
The kind of share an Allocation represents: direct (from the target's own metered usage), shared, idle or unallocated.
_Avoid_: kind, type

**Metered Share**:
A direct Allocation of an aggregated Billing Line, apportioned by each Query Execution's billable usage. It is an estimate, never an exact price.
_Avoid_: query cost, exact cost

**Billable Usage**:
The quantity a provider charges a Query Execution for, after the provider's minimums and rounding. It can differ from the quantity the query actually used.
_Avoid_: scanned bytes (unless that is what is billed)

**Coverage**:
The fraction of a Billing Line's billed usage quantity accounted for by the Billable Usage of known Query Executions. Only the covered fraction can become Metered Shares.
_Avoid_: match rate, completeness percentage

**Query-history Completeness**:
Coverage of exactly one for a Billing Line: every billable Query Execution in its scope is known.
_Avoid_: full history

**Confidence**:
A categorical grade of Evidence quality for an allocated amount: direct_verified, metered_estimate or heuristic_estimate. It does not apply to Unallocated amounts and is never a probability.
_Avoid_: score, likelihood

**Idle**:
A synthetic Allocation Target for cost of capacity that was paid for but had no observed activity.
_Avoid_: waste, unused (alone)

**Unallocated**:
A synthetic Allocation Target for cost whose Evidence is missing, ambiguous or ineligible. It is shown, never hidden or guessed.
_Avoid_: unknown, other, misc

**Allocation Policy**:
A versioned, documented set of rules that decides Eligibility, weights and remainder handling. Every Allocation records the policy version that produced it.
_Avoid_: rules engine, config

**Calculation Run**:
One deterministic execution of the Allocation Policy over a fixed set of inputs.
_Avoid_: job, batch

## Reconciliation

**Reconciliation**:
The check that Allocations sum exactly to the authoritative amount they partition, per currency, cost basis, period and source version.
_Avoid_: balancing, matching

**Reconciliation Delta**:
The signed difference between the authoritative amount and the sum of its Allocations. It is zero when Reconciliation holds.

**Ledger**:
The immutable, versioned record of Export Snapshots and the Allocation Snapshots computed from them. One of each is current; all are retained.
_Avoid_: database, warehouse

**Allocation Snapshot**:
The complete set of Allocations produced by one Calculation Run. Any period may be recalculated; doing so creates a new snapshot and moves the current pointer.
_Avoid_: result, rerun output

## Flagged ambiguities

- "Query cost" implies an exact per-query price that providers do not bill. Say "Allocation to a Query Execution" and state its Confidence.
- "Unknown" is used in fixtures as a target name; the domain term is Unallocated.
