# Progress log

2026-11-01: Initial design package created. No code implementation, upstream code extraction, live cloud deployment, or test execution performed. Next: M0.

## M-1 bootstrap (2026-11-01)
Scaffold, CI workflow, fixture arithmetic tests, golden scenarios, execution permission guidance, and architecture boundaries added. NOT complete: lockfile generation, actual test runs, remote repository setup, CI validation and real allocation engine.

## M-1 execution (2026-11-01)
Run locally on Windows 11 with uv 0.12.23 and CPython 3.12.15 (uv-managed). No cloud credentials used.

- `uv lock --python 3.12`: resolved 54 packages; dev-only (no runtime dependencies). Direct: bandit 1.9.4, build 1.6.1, hypothesis 6.170.14, mypy 1.20.2, pip-audit 2.10.1, pre-commit 4.7.0, pytest 9.2.0, pytest-cov 7.1.0, ruff 0.17.3. All from PyPI.
- `rm -rf .venv && uv sync --extra dev --frozen`: succeeded from the lockfile alone; `uv lock --check` passes.
- First gate run: mypy, pytest, pip-audit, bandit passed; ruff failed (6 lint errors, 4 files unformatted). Fixed: `AllocationKind` now uses `StrEnum` (UP042), fixture-loader lambda replaced with a `def` (E731), import sorting and long lines via `ruff check --fix` / `ruff format`.
- Final gate run:
  - `uv run ruff check .`: All checks passed!
  - `uv run ruff format --check .`: 55 files already formatted
  - `uv run mypy src`: Success: no issues found in 11 source files
  - `uv run pytest --cov=data_finops`: 4 passed; coverage 100% (16 statements, contracts only)
  - `uv run pip-audit`: No known vulnerabilities found (local package skipped: not on PyPI)
  - `uv run bandit -q -r src`: no issues
- Secret scan (gitleaks v8.24.2 via Docker): `git` mode over history: no leaks found. `dir` mode reported 200 findings, all under `.venv/Lib/site-packages/license_expression` (third-party license data, gitignored); zero findings in project files.
- Golden fixtures, checked independently by script against `golden-fixtures.md`:
  - `existing-fixtures-no-price-evidence`: per-line reconciliation holds against `fixtures/billing.json` (bill-001 6.00, bill-002 4.00, both unallocated; no query price inferred).
  - `explicit-synthetic-cost-pools`: direct 3.00, shared 2.00 split 1.25/0.75 (62.5%/37.5%), idle 1.00, unknown 4.00; total 10.00.
  - `negative-adjustment`: -1.00 retained signed, not clamped.
  - Gap: synthetic and credit records have no corresponding billing lines with period/source_version, so only aggregate reconciliation is checkable for those scenarios. The doc states aggregate equality is insufficient; add billing-line fixtures for them before M4. Since closed: every scenario in `fixtures/golden/scenarios.json` carries its own billing lines (`golden-fixtures.md`).

- CI on PR #1: first run failed at gitleaks (shallow checkout could not resolve the PR commit range); fixed with `fetch-depth: 0`. Both push and pull_request runs then passed.

Still open: GitHub branch protection (repository owner).

## Pre-implementation architecture review (2026-11-01)
Domain modeling and design review with the repository owner, following the grill-with-docs workflow. No production code written.
- Added `GLOSSARY.md` (domain language) and `docs/research/architecture-review.md` (12 internal contradictions, 10 standards conflicts, 9 proposals; now a historical record).
- Decided OQ-9 to OQ-19 and narrowed OQ-1 (`open-questions.md`); OQ-20 open, non-blocking.
- New ADRs: 0015 (FOCUS billing lines, export-snapshot revisions), 0016 (allocate to Query Executions, roll-ups above), 0017 (metered share requires measured Coverage).
- Updated specs 02–04, `acceptance.md`, `golden-fixtures.md`, `plan.md`, demo walkthrough, both schemas, the standards matrix and the golden scenario. The demo and golden oracle no longer conflict: the $6 Athena line is a metered estimate to `q-001` at Coverage = 1.
- Moved to M0: fixture rebuild in provider-native form and contracts generated from Pydantic models.

## M0: scaffold and contracts (2026-11-01)
Tests were written first and failed on missing modules before implementation. No cloud credentials used.

- **Standards gate (ADR 0014), verified upstream facts:** AWS Data Exports emits FOCUS 1.2 (`FOCUS_1_2_AWS`; AWS announcement 2025-11-19 and column dictionary, which has no row identifier and publishes costs as `double`). OpenLineage core spec 2-0-2 and ParentRunFacet 1-1-0; dbt's `dbt_run` facet carries `invocation_id` (openlineage-common, release 1.55.0). dbt manifest v12 and run-results v6 schemas; dbt's default query comment omits `invocation_id`. Athena field names from botocore (commit ce61e7c). Recorded in the standards matrix and build-vs-reuse register.
- **Dependencies:** runtime `pydantic` 2.14.2, `jsonschema` 4.26.0, `rfc3339-validator` 0.1.4 (all MIT, plus MIT transitive deps). The `jsonschema[format-nongpl]` extra was rejected (11 packages, one MPL-2.0, one with no declared license). pip-audit: no known vulnerabilities.
- **Contracts:** `data_finops.contracts` (billing, workload, allocation, types). Money is Decimal parsed only from decimal strings; currencies are ISO 4217; timestamps must be UTC. `FocusBillingLine` uses FOCUS column names and preserves all other columns. `Allocation` enforces the category, target and confidence rules in the model and in the generated schema.
- **Schemas:** `schemas/{allocation,billing-line,export-snapshot}.schema.json` generated by `uv run python -m data_finops.schemas`; a test fails on drift or on unpublished schema files. The interim billing-line shape is replaced by FOCUS 1.2 here rather than at M1.
- **Fixtures rebuilt in provider-native form:** FOCUS export snapshot (Athena and S3 lines, with ConsumedQuantity); Athena BatchGetQueryExecution response with two queries for one model (DML and DDL) carrying the dbt query comment; OpenLineage DAG → task → dbt run → model chain with `parent` and `dbt_run` facets and OpenLineage dataset names; dbt manifest and run results. Golden scenarios moved to the Allocation contract with per-line billing, and the synthetic scenario now targets Query Executions.
- **Readers:** `billing.focus`, `providers.athena`, `lineage.openlineage`, `dbt.artifacts`.
- **Import boundaries:** static test enforcing architecture-boundaries.md (contracts are leaves; attribution imports no providers, API or network modules and reads no clock; billing and evidence packages stay independent).
- **CI:** added bandit and a CycloneDX SBOM (pip-audit, uploaded as an artifact).
- `uv run python scripts/verify_upstream.py` (network): manifest v12 ok; run-results v6 ok; 5 OpenLineage events ok against RunEvent; 3 parent facets ok against ParentRunFacet; 0 failures. Deliberately broken copies (missing `fqn`, bad status, non-UUID runId, parent without `job`) were each rejected.
- New open question OQ-21 (FOCUS doubles to Decimal), needed at M1.

## Reference workload (2026-11-01)
Records for each step are on the issues; account and bucket identifiers stay out of the repository.

- #22: Pulumi stack, mock tests; `pulumi preview` and, with owner approval, `pulumi up` (8 resources) recorded on #22 and #24.
- #23: dbt-athena project; offline parse tests render the exact query comment Athena receives. dbt-athena omits it for `alter`/`drop`/`optimize`/`vacuum`/`msck` (OQ-22).
- #24 run record: seed and two runs via `dbt-ol` in Docker. 20 Athena queries, 17 commented; one model issued 3 queries on an incremental run; 22 OpenLineage events valid upstream; dbt 1.12.6 artifacts exceed their published schemas (OQ-23).
- #29: `scripts/verify_upstream.py` allows additions in dbt artifacts and checks real outputs (`--dbt`, `--openlineage`, JSON Lines). `uv run python scripts/verify_upstream.py`: 0 failures on fixtures; with `--dbt .local/dbt/{1-seed,2-run,3-run} --openlineage .local/openlineage/dbt-events.jsonl`: 44 checks, 0 failures. A real run-results copy with `status` removed and a string `execution_time` fails with 2 errors.
- #31: the lineage reader accepts JSON Lines and the Athena reader a plain BatchGetQueryExecution response with caller-supplied account and region; both accept a UTF-8 byte-order mark (PowerShell 5.1 `Out-File`). #24's raw `.local/` outputs load without conversion: 22 events, 20 query executions.
- #32: `scripts/anonymize_workload_outputs.py` anonymizes Athena history, dbt artifacts and OpenLineage events with the FOCUS anonymizer's pseudonyms; the shared account rule now leaves UUIDs whole (a UUID's last group can be 12 digits), bounded so an account ID next to hex text is still replaced. SQL in Athena, dbt and OpenLineage is cut to a dbt comment and leading keywords. #24's outputs → `.local/workload-sample/`: 8 files, its own scan clean; an independent scan finds no account ID, bucket, container path, Windows user name, venv path or dbt `project_id`; 20 executions and 22 events read back; 17 comments still link to a run_results invocation and a manifest node; 20 OpenLineage `dbt_run` invocations match; `verify_upstream.py` 0 failures.
- #35: the anonymized sample is in `fixtures/reference-workload/` (Athena history, one manifest, three run results, OpenLineage JSON Lines; 716 KB). Account pseudonym replaced by the AWS placeholder and Athena timestamps converted to UTC for the public copy. A scan for the original account ID, bucket, local paths, user names, AWS profile name and dbt `project_id` found none. Contract tests pass; `verify_upstream.py` now checks the sample by default: 0 failures.
- #36: field allowlists for dbt manifests, run results and OpenLineage events (`scripts/anonymize_workload_fields.py`): unknown fields are dropped and counted by path, free text pseudonymized, unread dbt collections emptied; timestamps with a UTC offset become UTC. On #24's outputs the only non-`args` drop was `dbt_model.config.incremental` (then reviewed and allowed); scans clean; 17 comments still link; 20 OpenLineage invocations match; `verify_upstream.py` 0 failures. `fixtures/reference-workload/` regenerated with the new rules (messages, doc blocks and tag values now pseudonymized) and rescanned: clean.

## M1: offline billing ledger, verified against a real export (2026-11-05)
#7–#11 built the Ledger on hand-written fixtures. #12 closes M1 against the owner's real AWS FOCUS 1.2 Deliveries for 2026-10 and 2026-11. They were anonymized with `scripts/anonymize_focus_export.py`, and the anonymized copies, held in the git-ignored `.local/focus-anon/`, are what is verified. The raw Deliveries give the same results. Account IDs are masked below.

- **Manifest reader:** `data-finops ingest focus <export>-Manifest.json [--data-dir DIR]` reads one Delivery as an Export Snapshot.
  - Only the data files the manifest lists are read.
  - `published_at` is the execution's `CreatedAt`, taken from the S3 folder named in the manifest's `dataFiles` (ADR 0020 amendment: neither manifest records a publication time).
  - The FOCUS release must be the pinned 1.2.
  - A Delivery that is not as AWS writes it exits 2, naming the problem by file name.
  - `uv run pytest tests/integration/test_aws_data_exports.py` → 15 passed.
- **Commands, run on a new database:**
  ```text
  data-finops ingest focus .local/focus-anon/2026-10/data-finops-focus-1-2-Manifest.json --db m1.sqlite
  data-finops ingest focus .local/focus-anon/2026-11/data-finops-focus-1-2-Manifest.json --db m1.sqlite   (twice)
  data-finops ingest focus .local/focus-anon/2026-11-earlier/data-finops-focus-1-2-Manifest.json --db m1.sqlite
  uv run python scripts/reconcile_delivery.py .local/focus-anon/<period>/data-finops-focus-1-2-Manifest.json --db m1.sqlite
  ```
- **Output:**
  ```text
  focus: new aws-focus-<acct>-2026-10-6125b072-049f-358a-92f6-27ad54abd4f3
  focus: new aws-focus-<acct>-2026-11-980aa446-5e52-3927-9bca-c5774fc47fe8
  focus: replay aws-focus-<acct>-2026-11-980aa446-5e52-3927-9bca-c5774fc47fe8
  focus: superseded aws-focus-<acct>-2026-11-0771642f-2e82-3988-b27a-f8171b7deb3d
  2026-10  BilledCost     7984 lines  balanced  USD -0.0000169624 = -0.0000169624  lines with a non-zero delta: 0
  2026-10  EffectiveCost  7984 lines  balanced  USD -0.0000169624 = -0.0000169624  lines with a non-zero delta: 0
  2026-11  BilledCost     918 lines  balanced  USD -0.0000011680 = -0.0000011680  lines with a non-zero delta: 0
  2026-11  EffectiveCost  918 lines  balanced  USD -0.0000011680 = -0.0000011680  lines with a non-zero delta: 0
  ```
  - **Replay:** a second import of a Delivery stores nothing.
  - **Restatement in any import order:** the earlier 2026-11 Delivery (created 2026-11-05 13:06 UTC), imported after the later one, was stored as history, and the later one stays current.
- **OQ-21 confirmed:** of 91,091 doubles, none needs more than 15 significant digits for its shortest round-trip form, which has 1 to 12 decimal places.
  - Reconciliation compares the converted source with the Ledger, so it proves the Ledger round trip.
  - Fidelity to AWS's doubles rests on this shortest-form check, with the raw values kept in `data_finops_raw_values`.
- **Found on the way:**
  - The reader failed on AWS's zoned UTC timestamps on Windows, fixed in PR #136.
  - A Windows download cannot keep AWS's folder names, whose colons Windows forbids, hence `--data-dir` and reading the folder from the manifest.
- **Gates on the branch:**
  - `uv run pytest -q` → 716 passed;
  - `uv run ruff check .` → All checks passed;
  - `uv run mypy src` → no issues in 38 source files;
  - `uv run bandit -q -r src scripts` → no issues.

## M2: workload metadata (2026-11-01)
Spec #40, tickets #41–#48; decisions in ADR 0022 and the ADR 0018 amendment (PR #39). Each ticket was written test-first at the agreed seam (the workload store's public interface on a temporary SQLite file), mutation-checked, and reviewed on the Standards and Spec axes; the mutation results are in each PR (#49–#55 and #48's).

- #41 store and Workload Runs; #42 per-event screening against the bundled OpenLineage schemas; #43 parent, root and children; #44 changed copies, run flags, SQL text and stack traces removed before storage; #45 Data Assets and Lineage Edges with provenance; #46 dbt run results into Model Executions; #47 dbt manifest nodes and dependencies.
- **Exit criterion** ("expected fixture lineage and statuses; invalid payload rejected"), #48. `uv run pytest tests/integration/test_m2_exit.py` → 7 passed. One store ingests the hand-written fixtures, the reference workload sample, the committed invalid-payload fixtures and two generated ones (an oversized event, a manifest v11), and shows:
  - 5 + 22 events accepted; from `invalid/mixed.jsonl` 3 accepted, rejected not JSON 1, invalid RunEvent 2, invalid ParentRunFacet 1, and unsupported (not rejected) JobEvent 1 and DatasetEvent 1; from `invalid/array.json` invalid RunEvent 2; the oversized event too large; the manifest v11 rejected as `unsupported manifest v11`;
  - the DAG → task → dbt run → model chain with roots; the sample's three dbt runs completed without flags, the seed run without children and each run with its four model runs;
  - Model Executions 2, 4, 4, all `success`, and a generated run-results file keeping `error` and `skipped`; six manifest nodes; daily_orders reading stg_orders and writing daily_orders.
- Gates on the final branch: `uv run pytest -q` → 233 passed; `uv run ruff check .` → All checks passed; `uv run ruff format --check .` → 101 files already formatted; `uv run mypy src` → no issues in 25 source files; `uv run bandit -q -r src scripts` → no issues.
- Not in M2 (per spec #40): Identity Links (M3), HTTP receiver and command line (M5), relation-name mapping, published workload schemas. OQ-22 (queries dbt-athena sends without a comment) moves to M3, where correlation happens.

## M3: query correlation (2026-11-02)
Spec #60, tickets #61–#65; decisions from the design session recorded in PR #59 (GLOSSARY: Trusted Query Comment, Link Outcome; OQ-2 and OQ-22 updated). Each ticket was written test-first at the agreed seam (the workload store's public interface on a temporary SQLite file), mutation-checked, and reviewed on the Standards and Spec axes; the mutation results and review fixes are in each PR (#67, #69, #71, #72 and #65's).

- #61 Athena query history ingested per entry, storing only allowlisted metadata (no query text, `ExecutionParameters` or failure messages); #62 dbt-ol model runs linked to Model Executions; #63 Query Executions linked by Trusted Query Comment (first line only, hardened against forged comments), with link-outcome fixtures in their own workgroup so the golden Athena line keeps Coverage 1; #64 dbt invocations linked to the Workload Run that reported them.
- **Exit criterion** ("exact links match; missing or ambiguous Evidence makes no Identity Link and its Link Outcome is reported"), #65. `uv run pytest tests/integration/test_m3_exit.py` → 5 passed. One store ingests the hand-written fixtures and the reference workload sample, and shows:
  - reference workload: 17 of 20 Query Executions `linked`, 3 `no_evidence` (DDL scanning 0 bytes; the fixture README identifies them as the uncommented `alter table` statements, OQ-22); every linked query rolls up to exactly one of the sample's Model Executions, `daily_orders` with 3 queries in the second run; both `dbt-ol run` invocations linked to their `dbt-run-reference_workload` runs, each with 4 model runs linked to that invocation's 4 Model Executions; the seed invocation `unmatched` (dbt-ol seed emits no `dbt_run` facet);
  - hand-written: `q-001` → Model Execution (`inv-001`, `model.demo.orders`), the same one dbt-ol's model run reports → invocation run `dbt-run-demo` → parent Airflow task `daily_orders.dbt_run`, with root DAG `daily_orders` as the ParentRunFacet names it (the fixture has no events for the DAG run itself);
  - `q-003` `unmatched` and `q-004` `ambiguous`, neither linked; `q-004` is not among the queries of `model.demo.orders` although one report named it.
- Gates on the final branch: `uv run pytest -q` → 310 passed; `uv run ruff check .` → All checks passed; `uv run ruff format --check .` → 108 files already formatted; `uv run mypy src` → no issues in 26 source files; `uv run bandit -q -r src scripts` → no issues.
- Not in M3 (per spec #60): allocation, Coverage and Billable Usage, and whether `no_evidence` queries count against Query-history Completeness (M4, OQ-22's remaining half); fetching query history from AWS (M6); a real Airflow run (#66, OQ-2's Airflow half); HTTP receiver and command line (M5). Known limits: any run carrying a `dbt_run` facet without `dbt_node` is a candidate for its invocation whatever its producer (standards conformance matrix); a database file created before M3's columns is refused and must be recreated (no migrations yet).

## M4: allocation engine (2026-11-03)
Spec #80, tickets #81–#90 (#91 waits on #25); decisions from the design session recorded in PR #79 (ADR 0023; spec 03 "M4 structure"; OQ-22 decided). Each ticket was written test-first at the agreed seams (the pure engine; the Calculation Run on a temporary SQLite file), mutation-checked, and reviewed on the Standards and Spec axes; the mutation results and review fixes are in each PR (#92–#100 and #90's).

- #81 engine for a fully covered line, largest-remainder rounding per line and Cost Basis, ISO 4217 minor units; #82 Athena Billable Usage policy; #83 Athena billing scope (usage account, region, workgroup when the line names one, charge period); #84 Coverage below, at and above one with the export's half-unit tolerance; #85 Adjustments Unallocated and signed; #86 explicit direct, shared and idle pools; #87 Allocation Snapshots in the Ledger (ADR 0020's replay rule); #88 the Calculation Run in its own package (versioned line classification, input digest over what the engine is given); #89 Roll-ups read from the Identity Links each Allocation records as Evidence.
- **Exit criterion** ("all accounting invariants and negative adjustment tests pass; tests call the engine and match the golden scenarios"), #90. `uv run pytest tests/integration/test_m4_exit.py` → 4 passed:
  - `existing-fixtures-complete-coverage` end to end: every hand-written fixture ingested, a Calculation Run stores $6 to `q-001` (Coverage 1, `metered_estimate`, with its Model Execution and three Workload Runs as Evidence) and $4 Unallocated `ineligible`, matching the oracle; a second run replays;
  - `explicit-synthetic-cost-pools` and `negative-adjustment` computed by the engine and matching the oracle;
  - in all three scenarios, every line partitioned exactly in its currency and sign for all four Cost Bases, every Allocation valid against the published schema, no Confidence on Unallocated amounts;
  - Roll-ups: $6.00 per Cost Basis to `model.demo.orders` and to the dbt run, the Airflow task and the DAG run.
- Gates on the final branch: `uv run pytest -q` → 450 passed; `uv run ruff check .` → All checks passed; `uv run ruff format --check .` → 126 files already formatted; `uv run mypy src` → no issues in 30 source files; `uv run bandit -q -r src scripts` → no issues.
- Not in M4 (per spec #80): Evidence-linked credit allocation, shared or idle pools derived from billing, heuristic allocation, pools other than Athena on-demand, API and command line (M5), AWS fetching (M6). Known limits: the Athena policies are provisional until #91; a database file created before M4's tables gains them on open, but one created before M3's columns is still refused.

## M5: API and HTML demo (2026-11-05)
Spec #103, tickets #104–#119; decisions from the design session recorded in PR #102 (ADR 0024; spec 06; GLOSSARY: Cost Summary, Cost Explanation; OQ-6 decided for local use). Each ticket was written test-first at the agreed seams: the read model, the API's test client and `main(argv)` on a temporary SQLite file. Each was mutation-checked and reviewed on the Standards and Spec axes; the mutation results and review fixes are in each PR (#120–#134).

- **What was built:**
  - #104 one OpenLineage event ingested from bytes;
  - #105–#106 the Cost Summary, in the read model and over HTTP;
  - #107 the OpenLineage receiver, `POST /api/v1/lineage` (1 MiB, Problem Details);
  - #108 the bearer token (ADR 0024);
  - #109 the Allocations list;
  - #110 the Cost Explanation;
  - #111 Roll-ups;
  - #112 reconciliation;
  - #113 provenance and the restated fixture;
  - #114 lineage;
  - #115–#116 the command line: `ingest`, `calculate`, `serve` (loopback by default) and `demo`;
  - #117–#118 the dashboard: the Cost Summary, then the Cost Explanation and lineage on demand, after Tufte and Few (spec 06's eight rules, `api/dashboard/DESIGN.md`).
  - The OpenAPI 3.1 document is checked in as `schemas/openapi.json` and tested for drift.
- **Exit criterion** ("the walkthrough passes as written"), #119. `examples/demo/README.md` is rewritten as `data-finops` commands, with what to see at each step. `uv run pytest tests/integration/test_m5_exit.py` → 7 passed. Each step runs through the command line and is read back through the API's test client:
  - steps 1–2 print `focus: new aws-focus-123456789012-2026-10-v1` first and `calculate: … new calc-…` last;
  - the bill is $10.00, with a Reconciliation Delta of 0.00, under its Calculation Run, time and policy version;
  - `q-001` gets $6.00 Direct, metered estimate, Coverage 1. Its Cost Explanation holds the $6.00 Billing Line, the Allocation, the Query Execution, Model Execution `model.demo.orders`, and the dbt run, Airflow task and DAG run, nearest first. The Roll-ups are $6.00 each, to `["inv-001","model.demo.orders"]` and to the three Workload Runs;
  - `model.demo.customers` is claimed only by `q-004`, `ambiguous`, and has no Roll-up. `model.demo.orders` is claimed by `q-001`, `linked`;
  - $4.00 is Unallocated, `ineligible`;
  - running every ingest and the calculation again prints `replay`, and the Cost Summary is unchanged;
  - `export-2026-10-restated.json` makes `aws-focus-123456789012-2026-10-v2` current, and the bill becomes $11.00. The previous Calculation Run is kept, with `is_current: false` and `superseded_by` naming the new run, and its $6.00 and $4.00 Allocations can still be read.
  - Mutation checks: 2 killed, 0 survived. Each step-7 replay storing a new Allocation Snapshot was caught, and so was provenance losing `superseded_by`.
- **The README Quick start, run from a clean clone** of the branch: `git clone`, then `uv sync --extra api --frozen` (5 s), then `data-finops demo --port 8765`.
  - It printed each ingest, `calculate: … new calc-…` and `serving … at http://127.0.0.1:8765/`.
  - `/health` → `{"status":"ok"}`; `/` → 307 to `/dashboard/`.
  - `/v1/costs/summary` → billed 10.00, allocated 6.00 at Coverage 1, Unallocated 4.00 `ineligible`.
  - Headless Edge rendered the live dashboard: `docs/implementation/screenshots/m5-exit-live-demo.png`.
- **Manual browser pass against the five-minute budget (spec 01, "Offline demo <= 5 minutes on developer laptop after dependencies installed"):** passed by the owner on 2026-11-06. The dashboard loaded near instantly, well within the budget. Screenshot: `docs/implementation/screenshots/m5-manual-pass.png`.
  - **Bug found on the first try:** the dashboard showed "Could not load: The request could not be completed." About half of its concurrent requests returned 500, 93 of 180 in a reproduction against `data-finops demo`.
    - Cause: FastAPI ran the sync `Reporting` dependency and the sync endpoint in different threadpool threads, and SQLite refuses a connection used outside the thread that opened it (`ProgrammingError`, hidden behind the 500).
    - Why it was missed: the test client and the headless screenshot sent one request at a time.
    - Fix: each endpoint opens and closes its own `Reporting`. After it, 180 of 180 returned 200.
    - `tests/integration/test_api_concurrency.py` sends the dashboard's reads, a Cost Explanation and a receiver write concurrently through `httpx.ASGITransport`. It fails on the old code and passes on the new.
  - **What local timing does not show:** the fixtures are two lines. On the owner's real anonymized 2026-10 Delivery (7,984 Billing Lines, one small account), from a new database:
    ```text
    ingest focus           1.35 s
    calculate              1.17 s
    GET /v1/costs/summary  0.65 s
    GET /v1/allocations    0.28 s   (Unallocated, 1,000 per page)
    GET /v1/reconciliation 0.83 s   (100 per page)
    GET /v1/rollups        0.28 s
    ```
    A large account bills millions of lines a month. Throughput at that size is not measured; it belongs with M6's deployment work.
- **Gates on the branch:**
  - `uv run pytest -q` → 724 passed;
  - `uv run ruff check .` → All checks passed;
  - `uv run ruff format --check .` → 163 files already formatted;
  - `uv run mypy src` → no issues in 38 source files;
  - `uv run bandit -q -r src scripts` → no issues.
- **Not in M5 (per spec #103):**
  - fetching from AWS, and deployment (M6);
  - a PyPI package;
  - authentication beyond one static token (ADR 0024).
- **Known limits:**
  - the Athena policies are provisional until #91;
  - with a token configured, a browser cannot send it, so the dashboard is for the token-free loopback demo;
  - the Workload Runs Roll-up names each run by its run ID, not by what it ran.

## D: documentation site (2026-11-06)
Spec 07 and ADR 0025 (design session, PR #141); spec issue #135; tickets #142–#160, plus #171 and #173, which the owner added on 2026-11-06. The tooling tickets were built test-first at spec 07's seams: the checker, `build` and the page runner. Each was mutation-checked and reviewed on the Standards and Spec axes. The content tickets followed AGENTS.md's milestone D exception: the checker, the page runner and one accuracy reviewer. Mutation results and review fixes are in each PR (#161–#170, #172, #174).

- **What was built:**
  - `scripts/site/` (stdlib only):
    - `check` covers links, structure, accessibility basics, WCAG contrast of the colour tokens, external URLs in CSS and JS, and account IDs.
    - `build` stamps the head, nav, "On this page", previous and next links, and footer, generates the CLI, HTTP API, data-contract and Glossary reference pages, and wraps code lines.
    - The page runner runs every `run` block through `main(argv)` and FastAPI's test client, and compares the `output checked` blocks.
  - Pages on GitHub Pages (`.github/workflows/pages.yml`, SHA-pinned actions): https://apmalong.github.io/data-finops/.
  - Content:
    - the tutorial "Your first allocation", which replaces `examples/demo/README.md` as the walkthrough and is run by `test_m5_exit.py`;
    - 10 how-to guides, 8 of them run by `test_site_how_tos.py`;
    - 5 reference pages, 4 generated and checked for drift;
    - 6 explanation pages, with two D2 diagrams.
  - The readable template (#171): wrapped code with hanging indents, a nav that expands only the current section, "On this page", a type scale.
  - Windows and macOS/Linux tabs for every command example (#173). `build` derives the PowerShell version by fixed rules or takes a hand-written one, and `site.js` remembers the reader's choice. Without the script, both versions show.
- **Exit criterion** (spec 07, "Exit"):
  - **Live on Pages from `main`:** Pages run 42257456740 (success) deployed `078a794`, the #174 merge.
  - **CI on the same commit:** run 42257456848 passed (`quality`: the checker over `site/`, the drift test, the page-runner tests for the tutorial and the how-tos, ruff, mypy, bandit, gitleaks).
  - **Light and dark screenshots of the live tutorial:**
    - `docs/implementation/screenshots/d-exit-light.png` and `d-exit-dark.png` (headless Edge, 1440px). They show the tabs, "On this page" and the collapsed nav.
    - The PRs add both tabs (`173-*.png`) and both widths (`171-*.png`).
  - **Keyboard-only pass through the nav:** passed by the owner on 2026-11-06.
  - **The tutorial followed by hand from the live site:** followed by the owner on Windows on 2026-11-06. The owner reported that everything works but did not record a time.
    - **Bug found on the first try:** the owner's first try stopped at step 3. In Windows PowerShell 5.1, `curl` is `Invoke-WebRequest`, which prompted for `Uri:`. #173 fixed it with the Windows tab's `curl.exe`.
    - **Scripted run as a timing reference:** a clean clone, `uv sync --extra api --frozen`, and every command copied from the live page's run blocks: 7 blocks, every command exiting 0, 17 s in all.
- **Found and fixed during D:**
  - **Stale reference page:** the HTTP page's Schemas section was missing, so responses named types the page never explained (#167).
  - **Wrong claims on pages:** Unallocated Allocations can carry Evidence, every Athena changed copy is flagged, and the bill diagram split one Billing Line across Athena and S3 (#169, #170).
  - **Secret scan:** gitleaks' `curl-auth-header` rule caught a dummy token written into a `curl -H` line. Pages now send it with `$(cat …)` (#169).
  - **Sticky column:** the "On this page" column used a grid trick that broke past 200 elements, and the HTTP page already had 183 (#172).
  - **Windows assignments:** `CALCULATION_RUN_ID=calc-<…>` passed into the Windows tab as bash (#174).
- **Known limits, left as they are:**
  - **CLI install hint:** it still says `pip install 'data-finops[api]'`, but nothing is on PyPI. The install page gives `uv sync --extra api`.
  - **#26** has no recorded teardown yet.
  - **The deploy page's Windows `PULUMI_BACKEND_URL`** is copied from the reference workload's README and not re-verified.
  - **"database file":** the site keeps the phrase. GLOSSARY lists "database" under _Avoid_ for Ledger, so this is a site-wide question.
  - **The HTTP API's parameters** have no descriptions in the OpenAPI document.

## M6: AWS deployment (2026-11-07)
Designed 2026-11-06 in PR #176 (spec 08; ADRs 0026–0028; GLOSSARY: Deployment, Collection Run, Inbox, Collection Watermark; the owner's decisions Q1–Q52 in `m6-decisions.md`). Spec issue #177; tickets #178–#193, in ten PRs, each against `main`. Every ticket but #191 (content) was built test-first at spec 08's seams, with hand-made mutation checks recorded in its PR and the Standards and Spec reviewers run in parallel. Nothing was deployed: no agent called AWS, and no `pulumi preview`, `up` or `destroy` ran against a real account.

- **What was built:**
  - #194 (#178) the minimal Cost Explanation for a Deployment (ADR 0028), and `serve --explanation-detail`;
  - #195 (#179, #180) the Collection Run, from the S3 Inbox: OpenLineage events and dbt artifacts;
  - #196 (#181) Athena query history in the Collection Run, and `data-finops collect`;
  - #197 (#182) FOCUS Deliveries in the Collection Run, with the Collection Watermark;
  - #198 (#183, #184) the Lambda handlers: the collector over the database object by S3 compare-and-swap, and the read-only API through Mangum;
  - #199 (#185, #188) `infra/pulumi/aws/`: the Pulumi project, the storage component, the policy pack and the `infra` CI job;
  - #200 (#186, #187) the Lambda package build and the compute component;
  - #201 (#189) the rest of the policy pack: 11 mandatory checks;
  - #202 (#190, #191) the generated Deployment settings reference, the deploy, read, teardown and Inbox how-tos, and the Deployment architecture explanation;
  - this PR (#192, #193) the owner's deploy-and-verify and teardown wizards, the draft deployer policy, and this record.
- **Exit criterion** (spec 08, "Exit"): tests 6–8 pass offline in CI, and the policy pack rejects the non-compliant fixtures.
  - **CI on `main`:** run 42414186977 on `6779b5f`, the #202 merge. Both jobs passed. Its `infra` job:
    ```text
    pulumi/actions@v6 → /usr/local/bin/pulumi version → Matched version: v3.272.0
    policy/  uv run mypy → Success: no issues found in 4 source files
             uv run pytest → 44 passed
             uv run pip-audit → No known vulnerabilities found
    stack    uv run mypy → Success: no issues found in 15 source files
             DATA_FINOPS_BUILD_PACKAGE=1 uv run pytest → 72 passed
               tests/test_compute.py      12  (seam 6)
               tests/test_package.py       6  (seam 8, the real arm64 package built from the checkout)
               tests/test_policy_pack.py  13  (seam 7b)
               tests/test_program.py       6  (seam 6)
               tests/test_settings.py     25  (seam 6)
               tests/test_storage.py      10  (seam 6)
             uv run pip-audit → No known vulnerabilities found
    ```
  - **The pack rejects the non-compliant fixtures** (seam 7b): `pulumi policy analyze` over the program's mock-recorded state passes all 11 checks, and each of 11 non-compliant fixtures exits 1, naming its policy:
    `s3-public-access-blocked`, `s3-guarded` (no versioning), `s3-encrypted`, `s3-tls-only`, `no-ruled-out-resources` (a VPC), `url-iam-only`, `function-bounded`, `function-logged`, `iam-least-privilege` (twice: the collector granted `athena:StartQueryExecution`, the API granted `s3:PutObject`), and `budget-alerts` (actual spend only). The pack's 44 unit cases are seam 7a.
  - **Locally, on this PR's branch** (Windows 11, uv 0.12.23):
    ```text
    root     .venv/Scripts/python.exe -m pytest -q → 977 passed
             ruff --no-cache check . / format --check . → All checks passed / formatted
             python -m scripts.site build → 0 page(s) written; python -S -m scripts.site check → 0 problem(s)
    stack    .venv/Scripts/python.exe -m pytest -q → 98 passed, 1 skipped (the real package build, which CI runs)
             mypy → Success: no issues found in 19 source files; bandit -q -r deployment __main__.py → clean
    policy/  unchanged since #201 → 44 passed
    ```
  - **Optional, not run:** the owner's deploy-and-verify wizard (`infra/pulumi/aws/wizards/deploy-and-verify.sh`). Spec 08 makes it optional for the exit. Its output will be added here only if the owner runs it.
- **Owner-only, still to do:**
  - **#26:** the teardown wizard (`wizards/teardown.sh`) destroys the reference workload and checks that nothing is left. It also tears down a Deployment if one was deployed. #26 does not block the exit. — **done 2026-11-07**, see below.
  - **Q22:** make `infra` a required CI check, after its first green runs (it has passed on every PR since #199).
- **Found and fixed during M6:**
  - **The first Collection Run would have looped:** without `s3:ListBucket`, S3 answers a missing object with `AccessDenied`, not `NoSuchKey`. Both roles get `ListBucket` on their own bucket (#198, #200).
  - **The reader policy would have got 403:** Function URLs created since October 2025 need `lambda:InvokeFunction` through the URL as well as `lambda:InvokeFunctionUrl` (#200).
  - **Kept log groups** would have collided with the next deployment's fixed names. Only the bucket and its settings are kept on destroy (#200).
  - **Policy 4's holes:** `NotAction`, `NotResource`, a single statement and wildcard ARNs passed, and one allowlist covered every role. It became one allowlist per policy that fails closed (#201).
  - **`pulumi policy analyze`** hands resource policies each resource's inputs, but stack policies its outputs. The fixtures change both (#201).
  - **The wizards' dry run** found `pulumi up` printing the stack outputs, bucket and URL included, before the wizard could mask them. It now passes `--suppress-outputs` (#192).
- **Known limits, left as they are:**
  - **The database is capped by `/tmp` at 512 MB:** no function sets ephemeral storage (#202's owner question).
  - **A producer cannot write with `kmsKeyArn` set:** its policy lacks `kms:GenerateDataKey`. The how-to says to grant it by hand (#202's owner question).
  - **Advisory at a real preview:** the TLS bucket policy and the IAM documents are computed from other resources' ARNs, so a real `pulumi preview` only advises on them. `analyze` in CI enforces them.
  - **The draft deployer policy is unvalidated** until the owner's Access Analyzer step and a first `pulumi up`.
  - **Mocks cannot see `retain_on_delete`:** the deploy wizard checks it on the real state.
  - **Not supported yet:** an export encrypted with SSE-KMS.
  - **Revisit triggers:** a database over 5 GB (one `PutObject`; multipart upload); Python 3.14 with Mangum.
  - **Pin exception:** pulumi-aws 7.52.1 is newer than the two-week rule allows (owner Q23).
  - **Spec 08's wording:** it credits EventBridge Scheduler with the retries of a failed run, which are Lambda's asynchronous retries (#202's owner question). The site's pages say it correctly.
  - **The Windows `PULUMI_BACKEND_URL`** form on the deploy page is not verified on a real run. — **verified 2026-11-07** in #26's teardown: Pulumi opens `file://C:/…` and refuses Git Bash's `file:///c/…`. The wizards had the second form, now fixed (see below).

## After M6: #91 and #26 (2026-11-07)
On the owner's instruction ("complete the remaining issues", with their AWS session). The account ID, bucket names and profile are masked here and stayed masked in the output shown; the raw files are under the git-ignored `.local/`.

- **#91, the Athena policies against the real export** (PR #204; spec 03 items 3, 8, 11; OQ-3, OQ-22, OQ-24, OQ-25):
  - **Read:** the 2026-11-07 21:20 November delivery (`aws s3 cp` of its data and metadata folders), and the reference workgroup's whole Athena history, 36 queries (`aws athena list-query-executions` and `batch-get-query-execution`), saved before the teardown.
  - **Found:** a query that scanned 0 bytes bills nothing, whatever its statement type, and a CTAS, an INSERT and an OPTIMIZE of a few hundred bytes bill 10 MB each. One line spans 00:00 to 05:00. Two queries that completed seconds before 20:00 were billed in the 20:00 line (OQ-25, open).
  - **Changed:** `athena-billable-usage/2` (0-byte queries free) and `athena-billing-scope/2` (pool label by charge period); `/1` stays callable.
  - **Run:** `data-finops ingest focus <delivery>/…-Manifest.json`, `ingest athena <history> --account-id … --region us-east-1`, `calculate`:
    ```text
    2026-11-01T21:00Z/22:00Z  6e-05 TB  Coverage 1   (1.83 and flagged under /1)
    2026-11-06T00:00Z/05:00Z  3e-05 TB  Coverage 1
    2026-11-02T20:00Z/21:00Z  2e-05 TB  Coverage 0   (OQ-25)
    ```
- **#26, the reference workload torn down:**
  - **Checked first:** the stack held 8 resources: the bucket and its public access block, ownership controls and encryption; the Glue database; the Athena workgroup; the provider and the stack. No Budget, and not the FOCUS export bucket, which stays.
  - **Destroyed:**
    ```text
    $ pulumi destroy --preview-only --suppress-outputs   → 8 to delete
    $ pulumi destroy --yes --suppress-outputs            → Resources: - 8 deleted (4s)
    ```
  - **Checked in AWS afterwards:** each read must fail with its service's not-found error:
    ```text
    gone: reference bucket            (s3api head-bucket: 404)
    gone: Glue database reference_workload   (EntityNotFoundException)
    gone: Athena workgroup data-finops-reference   (WorkGroup is not found)
    Budgets named data-finops*: 0
    ```
  - **Local state:** `pulumi stack rm --preserve-config --yes dev` removed the empty stack and kept the git-ignored `Pulumi.dev.yaml`. The site's Deploy the reference workload page brings it back.
  - **Not run through the wizard:** `teardown.sh` starts from the Deployment's stack, which was never deployed. The same steps and checks were run by hand.
- **Found on the way:** both wizards gave Pulumi `file://$LOCAL/pulumi` built from Git Bash's `pwd` (`/c/Users/…`), which Pulumi on Windows reads as `C:\c\Users\…`. They now use `pwd -W` where it exists, and the dry-run test hands them their folder as bash names it.

## Architectural drift review and M7 design (2026-11-07 to 2026-11-09)
- **Drift review (#207, PR #217):** every mismatch between the written architecture and the code is in `docs/research/architecture-drift-2026-11.md`, with how each was handled. Doc-only drift was fixed. Stricter import-boundary tests were added, and two tests that keep the boundaries document and AGENTS.md in step with `src/`. Code changes and decisions are #208–#216, and the Inbox cap's interplay with `/tmp` is on #206.
- **M7 design (PRs #218, #219):** spec 09 and ADRs 0029–0031; the owner's decisions Q53–Q72 are in `m7-decisions.md`; research in `docs/research/m7-aws-finops-tools.md` and `m7-attribution-precedents.md`. Spec issue #220. Nothing is built or deployed yet.
