# Build-versus-reuse decision register

This is a **living implementation gate**. Candidate references are not evidence of a completed source audit. For every new adapter/parser/serializer/algorithm, create or update a row **before** writing production code. Confirm exact upstream version/commit, license at the file level if copying, maintenance, tests and footprint. Statuses: proposed / approved / rejected / deferred / verified.

| Capability | Candidate | Current decision | Why / constraints | Evidence outstanding |
|---|---|---|---|---|
| FOCUS normalization | finopsfoundation/focus_converters; native FOCUS exports | **Approved (M0):** native AWS FOCUS 1.2 export; converter deferred (ADR 0015) | Avoid conversion when already FOCUS; preserve raw rows and mapping version | Release, supported AWS/GCP fields, tests, licenses, SHA |
| OpenLineage event generation | OpenLineage Python SDK | **Proposed:** dependency | Don't copy SDK internals | Pin release, schema compatibility, transitive deps |
| OpenLineage RunEvent validation (M2) | OpenLineage 2-0-2 RunEvent and ParentRunFacet 1-1-0 JSON Schemas (Apache-2.0), bundled in the package and sha-pinned; jsonschema 4.26.0 (already a runtime dependency) | **Approved (M2):** bundle the schemas, validate offline | The pins in `scripts/verify_upstream.py` already identify these exact files; bundling avoids a network fetch at ingest. Apache-2.0 notice goes in THIRD_PARTY_NOTICES | Notice text and sha check at build time |
| OpenLineage receiver | OpenLineage specification; optional Marquez | **Proposed:** small receiver | Customer-managed minimal footprint; schema validation mandatory | Spec version, facets and event compatibility tests |
| dbt artifacts | dbt's published manifest/run_results schemas; Elementary optional tables | **Approved (M0):** schema-driven reader; manifest v12 and run-results v6 pinned by sha256 | No mandatory Elementary install; avoid bespoke interpretation of artifacts | Pin dbt schema versions and fixtures; optional Elementary table version |
| Athena execution metadata | AWS SDK/API (boto3, Athena GetQueryExecution etc.) | **Proposed:** official SDK | Prefer API over scraping or copying collectors | API fields, permissions, throttling, cost correlation limits. #31 (gate checked, no new dependency): the reader takes the plain BatchGetQueryExecution JSON the AWS CLI writes, with account and region supplied; OpenLineage JSON Lines parsed with stdlib `json` per line, since openlineage-python is a client for emitting events and the product already models RunEvent (ADR 0009 keeps the core dependency-light) |
| Billing ledger/reconciliation | FOCUS semantics; database transaction patterns | **Proposed:** original | Need revisions, idempotency, authoritative totals | Accounting policy, golden tests, candidate library review |
| Identity correlation | OpenLineage facets; OTel IDs; provider query IDs | **Proposed:** original | No universal cross-system identity protocol | Evidence hierarchy and ambiguity tests |
| Shared/idle allocation | OpenCost (K8s only); FinOps guidance | **Proposed:** original for Athena | OpenCost does not directly allocate Athena workloads | Algorithm alternatives, fairness, cost pool conservation |
| SQL lineage | SQLGlot | **Deferred:** dependency | Avoid writing SQL parser; inference is nonauthoritative | Dialect accuracy, licensing, pinned version |
| Metadata catalog | OpenMetadata / DataHub | **Deferred:** optional APIs | No required graph service | API compatibility and deployment overhead |
| Dataset contracts | ODCS | **Deferred:** optional reader | Avoid proprietary contract schema | Schema pin and interoperability tests |
| Telemetry | OTel SDK/exporters | **Deferred:** dependencies | No custom trace protocol | Semantic convention and privacy review |
| API contracts | FastAPI OpenAPI 3.1; JSON Schema | **Proposed:** generated schema | Avoid manual incompatible API description | Contract tests, pinned FastAPI/Pydantic versions |
| SBOM/license | CycloneDX or SPDX tooling; license scanner | **Approved (M0):** pip-audit CycloneDX output uploaded by CI | Trace transitive dependencies and vendored source | CI output and policy for unknown licenses |
| Contract models and JSON Schema generation | Pydantic 2.14.2 (MIT) | **Approved (M0):** dependency | Typed models generate the published JSON Schemas (spec 02); no hand-maintained schemas | Verified 2026-11-01: lockfile pin, pip-audit clean |
| JSON Schema validation | jsonschema 4.26.0 with rfc3339-validator 0.1.4 (both MIT); `format-nongpl` extra rejected | **Approved (M0):** dependency | Format-asserting validation of fixtures and events. The extra pulled 11 packages, one MPL-2.0 and one with no declared license; only date-time checking is needed | Verified 2026-11-01: lockfile pin, pip-audit clean |
| Ledger store | SQLite (stdlib); DuckDB rejected | **Approved (ADR 0018):** stdlib | Concurrent readers with one writer across processes (WAL); DuckDB allows one writing process | Decimal-as-text round trip tests (M1) |
| Parquet reader | pyarrow 24.0.0 (Apache-2.0, no transitive dependencies) | **Approved (#11, ADR 0018):** dependency, reader only | Reads AWS FOCUS Parquet exports; confined to the FOCUS reader by an import-boundary test | Verified 2026-11-01: lockfile pin, pip-audit clean, reader tests (doubles, maps, multi-file order). AWS Data Exports manifest field names are not publicly documented; manifest parsing deferred to #12 against a real export |
| Export Snapshot content digest (replay detection) | RFC 8785 JSON Canonicalization Scheme and its libraries; sorted-key compact JSON with SHA-256 | **Approved (#8):** original, stdlib `json` and `hashlib` | Internal to the Ledger and never exchanged; after contract serialization the content is only strings, nulls, lists and objects, so sorted-key JSON is deterministic without RFC 8785 number handling | Revisit if the digest is exported or compared across implementations, or if non-string numbers enter `billing_content` |
| Reference workload infrastructure (ADR 0021) | Pulumi 3.273.0 and pulumi-aws 7.52.1 (both Apache-2.0); AWS CLI scripts; CloudFormation | **Approved (#22):** dependencies of `examples/reference-workload/` only | ADR 0002 already chose Pulumi Python; isolated in its own uv project so `data_finops` stays small (ADR 0009); local file state, no hosted control plane (ADR 0001) | Verified 2026-11-01: lockfile pin, pip-audit clean, Pulumi mock tests in CI; `pulumi preview` against the owner's account pending approval |
| Reference workload dbt project and lineage (ADR 0021) | dbt-core 1.12.6, dbt-athena 1.11.1, openlineage-dbt / openlineage-python / openlineage-sql 1.54.0 (all Apache-2.0); dbt v2; hand-written Athena SQL | **Approved (#23):** dependencies of `examples/reference-workload/` only; the product's own OpenLineage SDK use remains the separate proposed row above | dbt Core v1 because dbt-athena and openlineage-dbt target it and our readers pin manifest v12; real dbt and `dbt-ol` produce the artifacts and events the product must read. openlineage-sql has no Windows wheel, so openlineage-dbt and openlineage-sql install only off Windows (run `dbt-ol` in WSL/Linux); openlineage-dbt 1.55.0 skipped as under two weeks old | Verified 2026-11-01: lockfile pin, pip-audit clean, offline `dbt parse` tests in CI. dbt-athena omits the query comment for `alter`/`drop`/`optimize`/`vacuum`/`msck` (OQ-22). Real run outputs pending #24 |

## Required decision template
- **Capability / issue:**
- **Existing standard(s) and versions:**
- **Evaluated implementations and links:**
- **Maintenance, dependencies, security and licenses:**
- **Customer-managed runtime cost:**
- **Decision:** import / optional integration / narrowly vendor / original / defer
- **Rationale and rejected alternatives:**
- **Exact source tag/SHA and file provenance if applicable:**
- **Conformance/benchmark tests:**
- **Owner, date, status:**

## Stop conditions
Do not copy third-party code with unverified file-level license or notices. Do not implement a new wire format if a suitable supported standard exists without an ADR exception. Do not mark an implementation 'verified' without runnable test evidence. Do not introduce mandatory services for optional integrations without an ADR amendment.
