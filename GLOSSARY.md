# Data FinOps

Attributes authoritative cloud charges to the data workloads that caused them, without creating, losing or double-counting money. Terms here are settled; contested terms are tracked in `docs/implementation/open-questions.md` until resolved.

## Billing

**Billing Line**:
One FOCUS row from a provider's billing export: an authoritative charge in one billing currency for one charge period, carrying each FOCUS cost column. It is the unit that Reconciliation partitions exactly once.
_Avoid_: bill row, invoice line, cost record

**Export Snapshot**:
The complete set of Billing Lines a provider published for one billing account, Billing Period and source at one point in time. A restatement replaces the whole snapshot; earlier snapshots are retained.
_Avoid_: file, batch, version (alone)

**Delivery**:
One AWS Data Exports execution: the data files and manifests AWS writes for one billing period at one time. Read as one Export Snapshot, published when AWS created the execution (ADR 0020).
_Avoid_: export (for one execution), drop, upload

**Superseded Snapshot**:
An Export Snapshot kept as history because a later publication is current for its Snapshot Scope.
_Avoid_: old version, stale snapshot

**Snapshot Scope**:
The billing account, Billing Period and source an Export Snapshot covers, and so the Allocation Snapshots computed from it. At most one Export Snapshot and one Allocation Snapshot are current for a Snapshot Scope.
_Avoid_: partition, key (alone)

**Billing Period**:
The provider's invoicing period that a Billing Line belongs to. It may differ from the line's charge period.
_Avoid_: month (alone)

**Cost Basis**:
Which FOCUS cost column a figure is taken from (for example BilledCost or EffectiveCost). Amounts in different cost bases are never added together.
_Avoid_: cost type, price

**Adjustment**:
A Billing Line that corrects or reduces charges: a credit, refund or discount (FOCUS ChargeCategory `Credit` or `Adjustment`). Adjustments are their own lines, signed, and may be negative. Tax and purchase lines are not Adjustments.
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

**dbt Node**:
A model, seed, test or other resource declared in a dbt project's manifest, identified by its node ID within one invocation's manifest. A dbt Node is a declaration; its execution in an invocation is a Model Execution.
_Avoid_: model (alone), dbt object

**Workload Run**:
One orchestrated execution reported by an orchestrator or lineage producer, such as an Airflow task run, possibly with a parent run (the run that directly contains it) and a root run (the top of its chain, such as the DAG run).
_Avoid_: job, task (alone), pipeline run

**Identity Link**:
An assertion that two workload identities (for example a Query Execution and a Model Execution) are the same unit of work, together with the Evidence for it.
_Avoid_: match, correlation (alone), join

**Evidence**:
The explicit identifier or trusted metadata that justifies an Identity Link or an Allocation. Time overlap alone is never Evidence.
_Avoid_: signal, heuristic

**Trusted Query Comment**:
The dbt comment at the very start of a query's text that names the dbt Node and invocation that issued it. A comment anywhere else in the text is not Evidence.
_Avoid_: query tag, query header

**Link Outcome**:
What became of an attempt to link a workload identity: linked (exactly one counterpart), no evidence, unmatched (Evidence names a counterpart nobody reported) or ambiguous (more than one candidate). Only linked makes an Identity Link.
_Avoid_: match status, correlation result

## Lineage

**Data Asset**:
A dataset or other data object that workloads read or write, identified within a namespace.
_Avoid_: table (alone), entity

**Lineage Edge**:
An observed read or write relationship between a Workload Run and a Data Asset. Lineage has no financial authority.
_Avoid_: dependency (alone)

## Allocation

**Allocation**:
The portion of one Billing Line, in one Cost Basis, assigned to one Allocation Target, with its method, Evidence and policy version. A Cost Pool only supplies the weights.
_Avoid_: charge, cost (alone)

**Allocation Target**:
What an Allocation is assigned to: a Query Execution, or one of the synthetic targets Idle or Unallocated. Model Executions and Workload Runs receive cost only as Roll-ups.
_Avoid_: owner, cost center, tenant, data product, consumer (not targets in the MVP)

**Roll-up**:
The sum of Allocations over the Query Executions linked to a Model Execution, Workload Run or higher grouping. A Roll-up is a view, never a further Allocation.
_Avoid_: re-allocation, inherited cost

**Cost Summary**:
Where one Allocation Snapshot put the bill, per currency and for one Cost Basis: the billed total, the allocated amounts by Allocation Category and Confidence, Unallocated amounts by reason, Adjustments, and the Reconciliation Delta.
_Avoid_: overview, total cost, spend report

**Cost Explanation**:
The chain behind one Allocation: its Billing Line, the Allocation's method, Confidence and Coverage, its Evidence resolved to Query Execution, Model Execution and Workload Runs, and the Roll-ups it contributes to.
_Avoid_: drilldown, trace, breakdown, query cost

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
The fraction of a Billing Line's billed usage quantity accounted for by the Billable Usage of known Query Executions in the line's billing scope (account, region, workgroup if billed, charge period), linked or not. Only the covered fraction can become Metered Shares. Scope is accounting scope, not Evidence (ADR 0023).
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
The check that what the product holds equals its authoritative source exactly, per Billing Line, currency and Cost Basis: the Ledger against the imported Export Snapshot, and Allocations against the Billing Lines they partition.
_Avoid_: balancing, matching

**Reconciliation Delta**:
The signed difference between the authoritative amount and what is held against it (the Ledger's amount, or the sum of Allocations), in one currency. It is zero when Reconciliation holds.

**Ledger**:
The immutable, versioned record of Export Snapshots and the Allocation Snapshots computed from them. One of each is current; all are retained.
_Avoid_: database, warehouse

**Allocation Snapshot**:
The complete set of Allocations produced by one Calculation Run. Any period may be recalculated; doing so creates a new snapshot and moves the current pointer.
_Avoid_: result, rerun output

## Deployment

**Deployment**:
One Pulumi stack of data-finops in one AWS account (ADR 0026, spec 08).
_Avoid_: install, environment

**Collection Run**:
One execution of the collector: it fetches every source a Deployment reads, ingests it, then starts a Calculation Run for each Snapshot Scope.
_Avoid_: job, sync, refresh

**Inbox**:
The bucket prefix producers write OpenLineage events and dbt artifacts to, for the next Collection Run to pull (ADR 0027).
_Avoid_: drop zone, landing area

**Collection Watermark**:
What a Deployment has already collected, per source: the Delivery folders ingested, the Athena query IDs stored and the Inbox objects processed, by key and ETag, so an overwritten object is collected again. It is never a clock time alone.
_Avoid_: checkpoint, cursor

**Public Demo**:
The hosted, read-only dashboard over the demo workgroup's share of a Deployment's bill, served as a static snapshot to anyone (ADR 0029, spec 09).
_Avoid_: hosted dashboard, live site

**Demo Snapshot**:
One published set of the read model's answers for the Public Demo, written after a Collection Run and swapped in whole.
_Avoid_: cache, export

**Simulated Orchestrator**:
The scheduled function that plays Airflow and dbt for the Public Demo: it runs believable DAGs whose models issue real Athena queries with real Trusted Query Comments, and writes their OpenLineage events and dbt artifacts to the Inbox (ADR 0030).
_Avoid_: fake Airflow, mock workload

## Flagged ambiguities

- "Query cost" implies an exact per-query price that providers do not bill. Say "Allocation to a Query Execution" and state its Confidence.
- "Unknown" is used in fixtures as a target name; the domain term is Unallocated.
- `no_evidence` names two things: a Link Outcome (a Query Execution without a Trusted Query Comment) and an Unallocated reason (a shared pool with no positive weight, #86). They never meet: Link Outcomes describe Identity Links, Unallocated reasons describe money. Say which one you mean.
- **Lineage Edge** was defined as a Workload Run's read or write of a Data Asset "or between two Data Assets". Only run-to-asset edges are stored (`contracts.workload.LineageEdge`), so the definition was narrowed to them (#207). Column-level lineage is the eventual goal (owner, 2026-11-07); it will need its own design and terms.
