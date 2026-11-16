# Data FinOps

Attributes authoritative cloud charges to the data workloads that caused them. It never creates money, loses money or counts money more than one time. The terms here are settled. Contested terms are in `docs/implementation/open-questions.md` until they are resolved.

## Billing

**Billing Line**:
One FOCUS row from a provider's billing export: an authoritative charge in one billing currency for one charge period, with each FOCUS cost column. It is the unit that Reconciliation partitions exactly once.
_Avoid_: bill row, invoice line, cost record

**Export Snapshot**:
The complete set of Billing Lines a provider published for one billing account, Billing Period and source at one point in time. A restatement replaces the whole snapshot. Earlier snapshots are kept.
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
The invoice period of the provider that a Billing Line belongs to. It can be different from the charge period of the line.
_Avoid_: month (alone)

**Cost Basis**:
Which FOCUS cost column a figure is taken from (for example BilledCost or EffectiveCost). Amounts in different cost bases are never added together.
_Avoid_: cost type, price

**Adjustment**:
A Billing Line that corrects or reduces charges: a credit, refund or discount (FOCUS ChargeCategory `Credit` or `Adjustment`). Adjustments are their own lines, signed, and can be negative. Tax and purchase lines are not Adjustments.
_Avoid_: rebate (unless the provider uses that term), negative cost

**Cost Pool**:
A set of eligible Billing Lines with one currency, cost basis, time window and pricing category, whose workloads give the weights. Each Billing Line in the pool is partitioned by those weights. The pool itself is not the partitioned amount.
_Avoid_: bucket, cost group

**Eligibility**:
Whether an allocator that exists can allocate a Billing Line to workloads. Ineligible lines remain Unallocated.

## Workload identity

**Query Execution**:
One provider-recorded execution of a query, identified by the provider's query execution ID within an account and region.
_Avoid_: query (alone), job

**Model Execution**:
One run of a dbt node within one dbt invocation. A dbt node ID alone never identifies an execution.
_Avoid_: model run, dbt job

**dbt Node**:
A model, seed, test or other resource declared in a dbt project's manifest, identified by its node ID within one invocation's manifest. A dbt Node is a declaration. Its execution in an invocation is a Model Execution.
_Avoid_: model (alone), dbt object

**Workload Run**:
One orchestrated execution that an orchestrator or lineage producer reports, for example an Airflow task run. It can have a parent run (the run that directly contains it) and a root run (the top of its chain, for example the DAG run).
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
What became of an attempt to link a workload identity: linked (exactly one counterpart), override (a person recorded the link with an Override), no evidence, unmatched (Evidence names a counterpart nobody reported) or ambiguous (more than one candidate). Only linked and override make an Identity Link.
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

**Cost Dimension**:
A business attribute an organization configures to group its cost by, such as team or tenant. Its value is a claim with a recorded source, never Evidence. A dimension is a view: it does not change how a Billing Line is partitioned (ADR 0031).
_Avoid_: attribution plane, tag (alone), label, Allocation Target

**Roll-up**:
The sum of Allocations over the Query Executions linked to a Model Execution, a Workload Run or a higher group. A Roll-up is a view, never a further Allocation.
_Avoid_: re-allocation, inherited cost

**Cost Summary**:
Where one Allocation Snapshot put the bill, for each currency and for one Cost Basis. It gives the billed total, the allocated amounts by Allocation Category and Confidence, the Unallocated amounts by reason, the Adjustments, and the Reconciliation Delta.
_Avoid_: overview, total cost, spend report

**Cost Explanation**:
The chain behind one Allocation. It gives the Billing Line, and the method, Confidence and Coverage of the Allocation. It also gives its Evidence resolved to Query Execution, Model Execution and Workload Runs, and the Roll-ups that it contributes to.
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
A synthetic Allocation Target for cost whose Evidence is not there, is ambiguous or is ineligible. It is shown, never hidden or guessed.
_Avoid_: unknown, other, misc

**Allocation Policy**:
A versioned, documented set of rules that decides Eligibility, weights and how remainders are handled. Every Allocation records the policy version that produced it.
_Avoid_: rules engine, config

**Calculation Run**:
One deterministic execution of the Allocation Policy over a fixed set of inputs.
_Avoid_: job, batch

## Reconciliation

**Reconciliation**:
The check that what the product holds equals its authoritative source exactly, for each Billing Line, currency and Cost Basis. It checks the Ledger against the imported Export Snapshot, and Allocations against the Billing Lines that they partition.
_Avoid_: balancing, matching

**Reconciliation Delta**:
The signed difference between the authoritative amount and what is held against it (the Ledger's amount, or the sum of Allocations), in one currency. It is zero when Reconciliation holds.

**Ledger**:
The immutable, versioned record of Export Snapshots and the Allocation Snapshots computed from them. One of each is current, and all are kept.
_Avoid_: database, warehouse

**Allocation Snapshot**:
The complete set of Allocations produced by one Calculation Run. Any period can be calculated again. A new calculation with different inputs creates a new snapshot and moves the current pointer. A replay of the current snapshot stores nothing.
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
What a Deployment has already collected, for each source: the Delivery folders ingested, the Athena query IDs stored, and the Inbox objects processed. Inbox objects are keyed by key and ETag, so an overwritten object is collected again. It is never a clock time alone.
_Avoid_: checkpoint, cursor

**Public Demo**:
The hosted, read-only dashboard over the demo workgroup's share of a Deployment's bill, served as a static snapshot to anyone (ADR 0029, spec 09).
_Avoid_: hosted dashboard, live site

**Demo Snapshot**:
One published set of the read model's answers for the Public Demo, written after a Collection Run and swapped in whole.
_Avoid_: cache, export

**Simulated Orchestrator**:
The scheduled function that plays Airflow and dbt for the Public Demo. It runs believable DAGs whose models issue real Athena queries with real Trusted Query Comments. It writes their OpenLineage events and dbt artifacts to the Inbox (ADR 0030).
_Avoid_: fake Airflow, mock workload

## Control plane

**Control Plane**:
The CLI commands, API routes and Deployment Dashboard actions that configure, run, inspect and correct a local Database or a Deployment (ADR 0032).
_Avoid_: admin panel, management API

**Command**:
One recorded request to change what data-finops holds or does, such as a run, a new Cost Dimensions version or an Override. The collector applies it, or rejects it with a reason. It is never changed after it is written.
_Avoid_: job, request (alone), action (alone)

**Override**:
A recorded replacement of one input of a Calculation Run, a Cost Dimension value or an Identity Link, for one open Billing Period or from a period onward. It never changes a billed amount.
_Avoid_: adjustment (that is a Billing Line), manual allocation, correction (alone)

**Control Role**:
What a person or principal can do in the Control Plane: reader, consumer or operator.
_Avoid_: permission level, user type

**Deployment Dashboard**:
The dashboard of one Deployment, served behind a sign-in, with the actions of the viewer's Control Role. It is not the Public Demo.
_Avoid_: hosted dashboard, admin console

## Flagged ambiguities

- "Query cost" implies an exact per-query price that providers do not bill. Say "Allocation to a Query Execution" and state its Confidence.
- "Consumer" is a Control Role. The Allocation Target _Avoid_ line uses "consumer" for a data consumer as a target, which is a different thing.
- "Owner" is not a term (Q156). `override dimension --owner` names the Cost Dimension that the configuration marks as the owner.
- "Unknown" is used in fixtures as a target name. The domain term is Unallocated.
- `no_evidence` names two things: a Link Outcome (a Query Execution without a Trusted Query Comment) and an Unallocated reason (a shared pool with no positive weight, #86). They never meet: Link Outcomes describe Identity Links, Unallocated reasons describe money. Say which one you mean.
- **Lineage Edge** was defined as a Workload Run's read or write of a Data Asset "or between two Data Assets". Only run-to-asset edges are stored (`contracts.workload.LineageEdge`), so the definition was narrowed to them (#207). Column-level lineage is the eventual goal (owner, 2026-11-07). It will require its own design and terms.
