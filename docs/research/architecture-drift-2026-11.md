# Architectural drift review, 2026-11-07 (#207)

**Status:** review note. It compares what the repository says about its architecture with what the code does after M6, #91 and #26.

**Method:**
- Four read-only reviews ran in parallel, one per group of sources: ADRs; specs and GLOSSARY; boundaries, modules and diagrams; `infra/`, dependencies and the site.
- Each finding was checked against the code, with a file and line, before it was recorded here.
- Items already tracked in #206 (the 512 MB `/tmp`, the producer's KMS grant, OQ-25, spec 08's retry wording, policy 3's bounds) are not repeated, except where this review adds to them.

**Outcome column:**
- **Fixed** means corrected in the same PR.
- **#N** means a ticket for a code change or an owner decision.
- **Guard** means a test added in the same PR.
- **Intended** means the difference is deliberate.

## ADRs (`docs/adr/`)

ADR decisions are not rewritten (`docs/adr/README.md`): a correction to a status line or a `*verify*` marker is made in place, and anything else becomes a dated note.

| ADR | Says | Code does | Outcome |
|---|---|---|---|
| 0013 | CI must fail on a stale lockfile | Every CI job runs `uv sync --frozen`, which never compares `uv.lock` with `pyproject.toml` | #208 |
| 0026 | the collector "runs one Calculation Run"; "the 10 GB `/tmp` ceiling" | one per Snapshot Scope (`collection/run.py`); no ephemeral storage set, so 512 MB | Fixed (note); `/tmp` in #206 |
| 0027 with 0026 | Inbox files up to 512 MiB | downloaded into a 512 MB `/tmp` that also holds the database copy: the cap is unreachable in a Deployment | comment on #206; Fixed (note in 0026) |
| 0011, spec 04 | integrations implement capability-declared ports | no ports exist; one provider, plain functions | Fixed (spec 04 marks them not built; deferred until a second provider; the owner wants no new ADR for it) |
| 0023 | scope is the period "containing the query's completion" | correct for the code, but the real export billed two queries in the next hour (OQ-25) | Fixed (note); decision in #206 |
| 0015 | "FOCUS release pin pending"; two `*verify*` markers; schema "replaced by the pinned FOCUS schema" | FOCUS 1.2 pinned (#12); code-first schema (OQ-15) | Fixed (status, markers, note) |
| 0014 | "upstream versions unverified" | verified and pinned in the build-vs-reuse register | Fixed (status) |
| 0017 | billed quantity comparable "(*verify*)" | checked: ConsumedQuantity equals PricingQuantity, Coverage 1 on real lines (OQ-3, #91) | Fixed (marker) |
| 0018, 0024 | amendments not mentioned in their status lines | amended in M2 and by ADR 0027 | Fixed (status) |
| 0004 | a lightweight receiver ingests OpenLineage | a Deployment pulls from the Inbox instead (ADR 0027) | Fixed (note) |
| 0021 | a reference workload in the owner's account | torn down 2026-11-07 (#26) | Fixed (status) |

No drift found: 0001, 0002, 0003, 0005, 0006, 0007, 0008, 0009, 0010, 0012, 0016, 0019, 0020, 0022, 0025, 0028.

## Specs (`docs/specs/`)

| Source | Says | Code does | Outcome |
|---|---|---|---|
| 08 §Collection Run 4 and 5, GLOSSARY Collection Watermark, both D2 diagrams, the README's Mermaid | Inbox "keys" in the Watermark | keyed by key **and ETag**, so an overwritten object is collected again (`workload/store.py`, Q40) | Fixed |
| 08 §Collection Run 7 | warns when an Inbox key would expire | no such warning, and the collector isn't given `inboxRetentionDays`; three other warnings exist but were unlisted | Fixed (listed; the expiry warning marked not built); #211 |
| 08 §Collection Run 8 | "the Calculation Run ID" | a list, one per Snapshot Scope | Fixed |
| 06 | every read takes `calculation_run_id` | the Cost Summary takes neither it nor `source` | Fixed (per endpoint); #215 |
| 06 | every page carries `calculation_run_id` | the Workload Run listing doesn't: it reads the workload store, not a snapshot | Fixed |
| 06 | only `calculate` and `demo` start a Calculation Run | so does a Collection Run | Fixed |
| 05 | fixtures in the wheel "left to M6 packaging" | M6 decided no PyPI release (Q6) | Fixed |
| 04 | only `sql.query` and `errorMessage.stackTrace` are removed | `extractionError.errors[].stackTrace` too; the Athena allowlist also keeps `Retryable` | Fixed |
| 02 | `CostPool`, `QueryExecution`, `DataAsset` and `LineageEdge` fields | engine pools (no contract); `scanned_bytes`, `billable_bytes`, no `reservation_id`; DataAsset is namespace and name; edges are run↔asset by direction | Fixed |

There is no spec 00. No drift found: 01; 03 (methods, reasons, versions, digest); 07; the rest of 05, 06 and 08 (settings, IAM, pins, caps, source order, routes, exit codes, token rules).

## GLOSSARY

| Term | Says | Code does | Outcome |
|---|---|---|---|
| Allocation | "the portion of a Cost Pool" | the portion of one Billing Line; a pool only supplies weights | Fixed |
| Lineage Edge | "or between two Data Assets" | only run↔asset edges are stored | Fixed, and listed under Flagged ambiguities; column-level lineage is the eventual goal (owner) |
| Ledger, _Avoid_: database | — | "database" is used about 230 times for the SQLite file | #216 |
| missing terms | — | Replay and duplicate, Changed copy and Observation, Producer, Workload store, Input digest, Billing scope, Billing Line Detail | #216 |

## Boundaries and modules

| Source | Says, or the smell | Code does | Outcome |
|---|---|---|---|
| `architecture-boundaries.md` | no `contracts` line; `normalization` holds billing facts | `normalization` is an empty package nothing imports | Fixed (contracts added; normalization marked reserved); #214 |
| same | `reporting` answers from the stores | it also computes Roll-ups with `attribution.rollups` | Fixed |
| same | `cli` calls the stores' public functions | it also calls the `billing` readers | Fixed; deepening in #214 |
| same | `collection` sits over `providers.aws`, the stores and `calculation` | also the `billing` Delivery reader and `collection.remote` | Fixed |
| same | `lambda_handlers` are thin entry points | the API handler caches the database object through `providers.aws` | Fixed (doc); move in #214 |
| `test_import_boundaries.py` | — | no entries for `ledger` or `calculation`; `workload`, `identity`, `lineage` and `dbt` not barred from the layers above them; `api` not barred from `collection` or `billing` | Guard (the code already complied) |
| — | nothing checked the boundaries document or AGENTS.md against `src/` | — | Guard (both must name every package, and only packages that exist). Not guarded: the package names inside `system-context.d2`'s labels, which are free text; drift there is left to review. |
| modules (judgement) | duplicate manifest parsing; duplicate Billing Period helpers; a test-only `load_events`; three 512 MiB caps; duplicate summary printing | — | #214 |

## Diagrams

| Source | Says | Code does | Outcome |
|---|---|---|---|
| README Collection Run sequence | the collector fetches and writes the object | `collection.remote.run_remote_collection` does | Fixed |
| `system-context.d2` | no `reporting` → `attribution` edge | Roll-ups at read time | Fixed |

No drift found: `ingest-flow.d2` (order, tables, caps); `deployment.d2`; `coverage.d2`; `bill-to-roll-ups.d2`; the event-ingest, Calculation Run and read-path sequences. `database.d2` is test-checked.

## `infra/`, dependencies, site

| Source | Says | Code does | Outcome |
|---|---|---|---|
| build-vs-reuse register, spec 08 | runtime versions are locked and vetted | the Lambda package installs from PyPI with no lock (`infra/pulumi/aws/deployment/package.py`) | #209 |
| register row 34 | the reference workload's `pulumi preview` "pending approval" | deployed (#22, #24), torn down (#26) | Fixed |
| spec 08 §Layout, register | pulumi-aws 7.52.1, newer than the two-week rule allows (owner Q23) | `infra/pulumi/aws/pyproject.toml` and `examples/reference-workload` pin 7.52.1; recorded as an exception | Intended (Q23) |
| deploy how-to, stack README | a test checks the CLI against the SDK | it checks a literal `v3.272.0`; the reference workload pins SDK 3.273.0 | Fixed (wording); #210 |
| teardown how-to | checks in AWS "that nothing … is left" | checks the main resources in AWS, the rest through the state | Fixed (wording); #213 |
| bill-is-authoritative page | reconciled "when an Export Snapshot is loaded" | only tests and `scripts/reconcile_delivery.py` reconcile | Fixed (wording); #212 |
| security-model page | a Cost Explanation always shows the full line | ADR 0028: full in `serve` by default, minimal in a Deployment by default | Fixed |
| security-model page | "SQL and stack trace facets are removed" | the query text and stack-trace fields are removed; SQL inside an error message is not detected | Fixed |
| register | "only `cli` makes clients" | only `aws_clients`, for `cli` and `lambda_handlers` | Fixed |

No drift found:
- the policy pack's per-policy allowlists, in sync with `compute.py`'s grants;
- spec 08 §IAM, §Topology, §Settings and §Outputs;
- the root pins, register and `uv.lock`;
- ADR 0009 (no Pulumi in `src/`);
- the explanation pages on allocation, restatements, identity links and the deployment.

## Conformance matrix

| Row | Said | Outcome |
|---|---|---|
| Lineage | "Pending: the HTTP receiver (M5)" | Fixed: built (M5, ADR 0024) |
| HTTP API | "M5" | Fixed: implemented, OpenAPI drift-tested |
| Infrastructure | "M6" | Fixed: implemented, not yet deployed |

Not verified here: the CycloneDX spec version pip-audit writes, and the botocore commit the matrix cites.
