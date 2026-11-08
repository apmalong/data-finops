# Milestone plan and task ordering

## Standards-first gate (required at M-1 and before each integration)
Review ADR 0014, update the standards conformance matrix and build-versus-reuse register; pin upstream schemas and add contract tests as implemented. Never count an unverified upstream version as supported.

## M0: scaffold and contracts
Create `pyproject.toml`, package, CI, format/type/test setup, Pydantic models, JSON schema validation, fixture loader, provenance docs. Generate `schemas/` from the models with a CI drift check (spec 02). Rebuild fixtures in provider-native form: FOCUS billing lines with usage quantity, Athena executions carrying the query comment, OpenLineage events with parent and dbt facets, dbt artifacts valid against pinned schemas. **Exit:** `pytest` passes without cloud credentials; golden fixtures validate against generated schemas. **Status:** complete 2026-11-01 (progress.md).

## M1: offline billing ledger
Pin the FOCUS release AWS emits (ADR 0015). Load fixture Export Snapshots, retain every cost column and persist to SQLite (ADR 0018); read Parquet exports with pyarrow; no row deduplication (ADR 0019). Build ledger with Decimal and per-line reconciliation. **Complete 2026-11-05** (#6, #7–#12; real-export verification recorded in progress.md). **Exit:** zero delta per Billing Line, replay idempotent, restatement replaces the Export Snapshot and retains the previous one.

## M2: workload metadata
Parse dbt manifest/run_results, ingest OpenLineage event files (JSON array or JSON Lines; HTTP moves to M5), build the run/model/asset graph in the workload store (ADR 0018 amendment, ADR 0022). Identity Links wait for M3. **Complete 2026-11-01** (#40, #41–#48; exit criterion recorded in progress.md). **Exit:** expected fixture lineage and statuses; invalid payload rejected.

## M3: query correlation
Load Athena query history fixture, link explicit query IDs to dbt model execution and parent Airflow run. Identity Links are derived when queried, never inferred from time, workgroup or order (ADR 0006): Query Execution → Model Execution by Trusted Query Comment, dbt-ol model run → Model Execution and dbt invocation → Workload Run by dbt-ol facets (ADR 0022). **Exit:** exact links match; missing or ambiguous Evidence makes no Identity Link and its Link Outcome is reported (allocation of unlinked Query Executions is M4's, ADR 0016). **Complete 2026-11-02** (#60, #61–#65; exit criterion recorded in progress.md).

## M4: allocation engine
Implement Coverage-based Athena on-demand metering (ADR 0017) with Billable Usage from a versioned provider policy, and shared/idle/unallocated pools; per-line rounding at line scale, method/evidence, Allocation Snapshots. **Exit:** all accounting invariants and negative adjustment tests pass; tests call the engine and match the golden scenarios. **Complete 2026-11-03** against the fixtures (#80, #81–#90; exit criterion recorded in progress.md). The Athena policies were checked against the real 2026-11 export in #91 (2026-11-07): `athena-billable-usage/2` and `athena-billing-scope/2`.

## M5: API and HTML demo
FastAPI OpenLineage receiver (`POST /api/v1/lineage`, one event per request, reusing M2's ingest path; loopback and token per ADR 0024, deciding OQ-6), `/health`, `/v1/costs/summary`, `/v1/allocations`, `/v1/lineage`, `/v1/reconciliation`, `/v1/provenance`, all answered by a `reporting` read model (spec 06); static dashboard (no JavaScript dependencies, works offline, designed after Tufte and Few, spec 06) and explain-cost drilldown; `data-finops` command line (`demo`, `serve`, `ingest`, `calculate`). FastAPI and uvicorn are an optional `api` extra. **Complete 2026-11-06** (#103, #104–#119; exit criterion and the owner's browser pass recorded in progress.md). **Exit:** local demo walkthrough passes (now the site tutorial, `site/tutorials/first-allocation.html`, run by `test_m5_exit.py`; #147).

## D: Documentation site
A site for people who run data-finops, organised by Diátaxis (tutorials, how-to guides, reference, explanation): hand-written HTML in `site/`, deployed as committed to GitHub Pages from `main` (ADR 0025, spec 07, #135). A stdlib tool in `scripts/site/` generates the CLI, HTTP API, data-contract and Glossary reference pages, stamps the shared nav, and checks links, structure and privacy rules, with a drift test. The tutorial "Your first allocation" replaces `examples/demo/README.md` as the walkthrough's source, and a test runs its commands. **Exit:** the site is live on Pages; the site checks, drift test and page-runner tests pass in CI; a manual pass (light and dark screenshots, keyboard-only nav, the tutorial followed from the live site in under five minutes) is recorded in progress.md. **Complete 2026-11-06** (spec 07; #135, #142–#160, #171, #173; exit criterion and the owner's manual pass recorded in progress.md).

D's tickets went in eleven PRs (owner, 2026-11-05 and 2026-11-06; AGENTS.md): #144; #145; #146; #147; #148–#151; #152; #153–#155; #156–#159; #171 (the readable template) and #173 (Windows and macOS/Linux tabs), both added by the owner on 2026-11-06; #160.

## Reference workload (supports M2–M4 validation; ADR 0021)
Pulumi Python stack in `examples/reference-workload/` (S3, Glue database, Athena workgroup with a per-query scan limit) and a tiny dbt-athena project run with `dbt-ol`, as its own uv project. Produces real Athena billing lines, query history, dbt artifacts and OpenLineage events, anonymized before use. Airflow deferred until M3 needs it. **Exit:** stack previews and deploys only with owner approval; a run produces artifacts and events; Athena charges appear in the FOCUS export; teardown verified.

## M6: AWS deployment (optional after M5)
Pulumi Python components, private API, scheduled read-only ingestion, encrypted storage, cost limits, deployment docs and teardown. **Exit:** preview policies validated; deployment only with explicit user authorization and credentials.

Designed 2026-11-06 (spec 08; ADRs 0026–0028; research notes `docs/research/m6-*.md`): reusable components in `infra/pulumi/aws/` for one account; two Lambda functions without a VPC sharing one SQLite file in S3, the scheduled collector its only writer by S3 conditional write; a read-only JSON API behind an `AWS_IAM` Function URL, the dashboard used locally; ingress by scheduled pull from S3 Inboxes (ADR 0024 point 6); AWS collectors in `providers.aws` behind an `aws` extra, and `data-finops collect`; a minimal Cost Explanation by default in a Deployment (#110); a custom Python policy pack validated offline with unit tests and `pulumi policy analyze` on mock-generated state, in a new `infra` CI job. **Exit (refined):** tests of the components, the policy pack and the package pass offline in CI and the pack rejects non-compliant fixtures; the owner's deploy-and-verify wizard is optional and recorded only if run. #26 (reference workload teardown) joins M6 as its last owner-only step; #91 stays an M4 follow-up. **Status:** complete 2026-11-07 (progress.md): the `infra` job's tests 6–8 green on `main`, the pack rejecting its 11 non-compliant fixtures; the optional deploy wizard not run; #26 left to the owner.

The owner's M6 decisions (Q1–Q52) are logged in `m6-decisions.md`. M6's spec issue is #177; its tickets are #178–#193, plus #26. They go in ten PRs, each against `main`, one open at a time, each closing every ticket it contains with a section per ticket (owner, 2026-11-06): #178; #179–#180; #181; #182; #183–#184; #185 and #188 (infra scaffold, `infra` CI job, first policies); #186–#187; #189; #190–#191; #192 and #193. #26 is run by the owner after #192 and never blocks the exit. Only #191 (content pages) takes milestone D's content exception; every other ticket keeps the full loop.

## Deferred
GCP, Snowflake, dbt Elementary optional tables, dlt, Dagster, OTel, BI connectors, automatic optimization, multi-account aggregation, enterprise auth.

## Work decomposition
Each milestone must have issue-sized tasks: models, adapter, tests, docs. Prefer vertical runnable increments to scaffolding all deferred integrations.
