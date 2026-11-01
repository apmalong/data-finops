# Pre-implementation architecture review (2026-11-01)

**Status: complete (2026-11-01).** All proposals were accepted. P1–P6 and P8 are applied to their target documents and recorded in ADRs 0015–0017 where they met the ADR test. P7 (fixture rebuild) and P9 (generated contracts) are scheduled as M0 work in `plan.md`; the target Allocation contract is already in `schemas/allocation.schema.json`. Decisions are recorded against OQ-9 to OQ-19 in `docs/implementation/open-questions.md`; OQ-20 is open and non-blocking. This document is now a historical record: the specs, ADRs and `GLOSSARY.md` are authoritative.

Facts marked *verify* come from reviewer knowledge, not a pinned upstream source; they must be confirmed under ADR 0014 before they drive implementation.

## 1. Domain model

Five areas, and how money and identity flow between them:

```
Billing ──(Eligibility)──► Cost Pool ──(Allocation Policy)──► Allocations ──► Reconciliation
                                              ▲
Workload identity: Query Execution ─IdentityLink─► Model Execution ─IdentityLink─► Workload Run
                                              ▲
Lineage: Workload Run ─LineageEdge─► Data Asset   (descriptive only; no financial authority)
```

- **Billing** owns money. Nothing else creates or changes amounts (ADR 0003).
- **Workload identity** owns which units of work exist and how they link. Its output is Identity Links with Evidence.
- **Lineage** owns which Data Assets workloads touch. It may label and group, never weight money.
- **Allocation** owns how a Cost Pool is partitioned. It consumes Billing and Identity, and is a pure function of them (architecture-boundaries.md).
- **Reconciliation** proves conservation: every partitioned amount sums exactly to its source.

## 2. Contradictions inside the package

| # | Finding | Where | Severity | Question |
|---|---|---|---|---|
| C1 | The demo gives `model.demo.orders` the $6 Athena line via a metering rule; the golden oracle says the same $6 must stay unallocated. Both cannot pass. | `examples/demo/README.md` step 4 and `acceptance.md` demo scenario vs `golden-fixtures.md` and `scenarios.json` | High: blocks M3/M4 acceptance | OQ-10 |
| C2 | Three incompatible Allocation shapes. The schema uses `pool_id`, `target_id`, `category`, `confidence`, `method`, `policy_version`; the code uses `source_record_id`, `target`, `kind`; the golden fixtures use the code shape and do not validate against the schema. Spec 02 also lists `billing_source_refs`, `target_type`, `calculation_run_id`, `as_of`, which neither has. | `schemas/allocation.schema.json`, `contracts.py`, `scenarios.json`, spec 02 | High: M0 builds on it | OQ-15 |
| C3 | The reconciliation unit is ambiguous. Spec 03 and acceptance reconcile per Cost Pool; golden-fixtures.md reconciles per bill line. A pool of many lines that reconciles in aggregate can still misallocate between lines, and a correction to one line cannot be applied without recomputing the pool. | spec 03 principle 2, `acceptance.md`, `golden-fixtures.md` | High | OQ-9 |
| C4 | Quantizing to currency minor units cannot conserve sub-cent Billing Lines. Provider exports carry amounts at sub-cent precision (*verify* scale for CUR 2.0/FOCUS), so allocations rounded to cents cannot sum exactly to a $0.0000123 line. | spec 03 step G | High | OQ-13 |
| C5 | `unallocated` appears in both `category` and `confidence`. For an Athena per-query share, the pair (category, confidence) is undefined: spec 03 E calls it `estimated_metered_share`, and spec 03 never says whether it is `direct`. | `allocation.schema.json`, spec 03 | Medium | OQ-14 |
| C6 | The Athena fixture carries `dbt_node_id` and `dbt_invocation_id` as if the provider recorded them. Real query history holds only the query text, so the link must be derived (from a query comment). The fixture hides the correlation step that M3 exists to build. | `fixtures/athena_queries.json` | Medium | Proposal P7 |
| C7 | The chain from query to dbt to Airflow is broken in fixtures. The OpenLineage event has no parent facet and nothing referencing `inv-001`, so M3's "parent Airflow run" link has no Evidence to test. | `fixtures/openlineage.json` | Medium | Proposal P7 |
| C8 | The mission promises attribution to datasets, data products, tenants and downstream consumers. No entity, Evidence source or conservation rule exists for tenants, data products or consumers. Consumer attribution redistributes producer cost along lineage, which double-counts if shown beside direct allocations. | README mission vs spec 02 | Medium | OQ-11 |
| C9 | The ledger is described as both immutable and "replace on replay/correction". Spec 02 and 03 H reconcile this only implicitly. Undefined: late telemetry for an already-allocated period, and whether any period is ever closed. | spec 02, spec 03 H, plan M1 | Medium | OQ-16 |
| C10 | Reading the query comment requires collecting Athena query text, but ADR 0012 forbids exporting SQL literals. The collector boundary that extracts the comment and discards the text is not specified. | spec 04, ADR 0012 | Medium | Proposal P8 |
| C11 | One dbt model normally issues several Athena queries (for example CTAS plus rename or drop, and incremental merges; *verify* for dbt-athena). Fixtures and acceptance assume one query per model, and acceptance says "links to one execution". | `acceptance.md`, fixtures | Low | Proposal P7 |
| C12 | Idle has no real source in MVP scope. Athena on-demand has no paid-for capacity, so Idle can only arise from synthetic pools until capacity pricing (deferred) exists. | spec 01 scope, spec 03 F | Low | none (record only) |

## 3. Conflicts with open standards

| # | Standard | Conflict | Proposal |
|---|---|---|---|
| S1 | FOCUS (adopted by ADR 0014) | `BillingLine` uses non-FOCUS names: `usage_start/end` (FOCUS: ChargePeriodStart/End), `billed_cost` plus a `cost_basis` selector (FOCUS carries BilledCost, EffectiveCost, ListCost and ContractedCost as *separate columns on the same row*), `service` (ServiceName), `SKU` (SkuId), `billing_account` (BillingAccountId). There is no BillingPeriodStart/End, which reconciliation needs. | P1 |
| S2 | FOCUS | Spec 02 models credits and adjustments as *fields* on a line. FOCUS models them as *rows* distinguished by ChargeCategory (for example Usage, Purchase, Tax, Credit, Adjustment). Corrections to earlier periods arrive as rows marked by ChargeClass (*verify* values for the pinned release), not as a new version of the old row. | P1, P2 |
| S3 | FOCUS / AWS exports | Dedupe "by source key/version" assumes a stable per-row identifier. FOCUS defines none (*verify*), and provider line-item IDs are not documented as stable across export restatements (*verify*). Row-level versioning may be impossible; restatement is per export snapshot. | P2 |
| S4 | FOCUS | Provider extension columns use the `x_` prefix. Spec 02 `provider_metadata` and the matrix's `data_finops.*` prefix (OTel) are fine, but billing extensions should keep their FOCUS `x_` names, not move into a free-form object. | P1 |
| S5 | ISO 4217 | `currency` is unconstrained in both schemas. FOCUS requires ISO 4217 codes; the minor-unit rule in spec 03 G also depends on ISO 4217. | P5 |
| S6 | JSON Schema 2020-12 | `format: date-time` is an annotation, not an assertion, by default, so the current schemas accept any string as a timestamp. Amounts are untyped strings (`"abc"` validates). | P5 |
| S7 | OpenLineage | `DataAsset` invents "namespace, stable identifier". OpenLineage defines a dataset naming convention (for example an Athena-specific namespace form, *verify*). The run hierarchy uses the standard ParentRunFacet; spec 03 D says "explicit parent facets" but spec 02 `WorkloadRun.parent_run_id` does not say it comes from that facet. | P6 |
| S8 | OpenLineage | Dedupe "by event/run identity and content digest" is right, since OpenLineage has no event ID. The identity is (runId, eventType, eventTime) plus a digest. Spec 03 C should say so, and say whether a re-emitted event with more facets supersedes or is kept alongside the first. | P6 |
| S9 | dbt artifacts | `manifest.json` (v12) and `run_results.json` (v6) fixtures declare official schema URLs but omit required fields, so they will fail validation against the pinned dbt schemas M2 must use. | P7 |
| S10 | dbt query comments | The default dbt query comment includes `node_id` but not `invocation_id` (*verify*). Linking a Query Execution to a Model Execution (node and invocation) therefore needs a configured `query-comment`, which is a customer setup requirement not yet documented. | P7, OQ-2 |

## 4. Proposed amendments

None of these is applied yet. Each lands in its target document once its open question is answered.

| ID | Change | Target | Depends on |
|---|---|---|---|
| P1 | Make the canonical billing record a FOCUS row: FOCUS column names for the pinned release, every cost column kept, credits and adjustments as rows by ChargeCategory, provider extensions kept as `x_` columns. Add a Billing Period to the record. The Cost Basis becomes a *selection* of which FOCUS cost column a Ledger view reconciles. | spec 02 BillingLine, `billing-line.schema.json`, ADR 0014 note | OQ-12 |
| P2 | Define a revision as an *export snapshot* per (billing account, billing period, source), replaced as a whole on restatement, with prior snapshots retained. Correction rows in later periods are ordinary rows. Row-level dedupe applies only within one snapshot. | spec 02 storage, spec 03 A, plan M1 exit | OQ-12 |
| P3 | The reconciliation and allocation unit is the Billing Line. A Cost Pool decides weights; each line in the pool is partitioned exactly once by those weights. | spec 03 principle 2 and G, `acceptance.md`, `golden-fixtures.md` | OQ-9 |
| P4 | Quantize each line's allocations at that line's own decimal scale (never coarser than the currency minor unit), using largest remainder with stable target-ID tie-break. For negative amounts, apply it to the absolute value and restore the sign. | spec 03 G | OQ-13 |
| P5 | Constrain schemas: ISO 4217 `currency` pattern, decimal-string `pattern` for amounts, and validation with format assertion enabled. | both schemas, plan M0 | none |
| P6 | Use OpenLineage dataset naming for DataAsset identity, ParentRunFacet as the only source of `parent_run_id`, and (runId, eventType, eventTime, digest) as event identity. | spec 02, spec 03 C | none |
| P7 | Rebuild fixtures in provider-native shape: Athena executions carry the query comment, not pre-joined dbt fields; one model issues several queries; OpenLineage events carry the parent and dbt invocation facets; dbt artifacts validate against the pinned schemas. Document the required dbt `query-comment` config. | `fixtures/`, `golden-fixtures.md`, spec 04 dbt | OQ-10 |
| P8 | The collector extracts the correlation comment from query text and discards the text before persistence, unless the operator opts in. | spec 04 data protection, ADR 0012 | none |
| P9 | One Allocation contract, generated from code and checked against the committed JSON Schema in CI. The golden fixtures use that contract. | spec 02, schemas, `contracts.py`, `scenarios.json` | OQ-14, OQ-15 |

## 5. Assumptions challenged (not yet contradicted, but unproven)

- **Complete query history.** Metered sharing of an aggregated Athena line is only defensible if every billable query in the line's scope is known. Query history API retention is limited (*verify* duration), and workgroups may be shared with non-dbt users. Without a completeness check, the metered share overstates the known queries. This bears on OQ-10.
- **Athena billable bytes ≠ scanned bytes.** Per-query minimums and rounding mean `DataScannedInBytes` is not the billable quantity (*verify* current pricing rules). Failed, DDL and canceled queries differ in chargeability. These rules are provider pricing, so they belong in a versioned provider policy, not core.
- **Billing time alignment.** Which billing hour or day a query spanning a boundary falls into is undefined (start, end or split), and billing days are UTC.
- **"Collector outage does not affect pipeline runs."** This holds for pull collectors. For the push path (OpenLineage HTTP), it depends on the emitter's behaviour (buffering or dropping), which this project does not control. Document that, don't assert it.
