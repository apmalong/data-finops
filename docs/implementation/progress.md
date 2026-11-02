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
  - Gap: synthetic and credit records have no corresponding billing lines with period/source_version, so only aggregate reconciliation is checkable for those scenarios. The doc states aggregate equality is insufficient; add billing-line fixtures for them before M4.

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
