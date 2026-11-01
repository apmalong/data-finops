# Build-versus-reuse decision register

This is a **living implementation gate**. Candidate references are not evidence of a completed source audit. For every new adapter/parser/serializer/algorithm, create or update a row **before** writing production code. Confirm exact upstream version/commit, license at the file level if copying, maintenance, tests and footprint. Statuses: proposed / approved / rejected / deferred / verified.

| Capability | Candidate | Current decision | Why / constraints | Evidence outstanding |
|---|---|---|---|---|
| FOCUS normalization | finopsfoundation/focus_converters; native FOCUS exports | **Proposed:** native first, package second | Avoid conversion when already FOCUS; preserve raw rows and mapping version | Release, supported AWS/GCP fields, tests, licenses, SHA |
| OpenLineage event generation | OpenLineage Python SDK | **Proposed:** dependency | Don't copy SDK internals | Pin release, schema compatibility, transitive deps |
| OpenLineage receiver | OpenLineage specification; optional Marquez | **Proposed:** small receiver | Customer-managed minimal footprint; schema validation mandatory | Spec version, facets and event compatibility tests |
| dbt artifacts | dbt's published manifest/run_results schemas; Elementary optional tables | **Proposed:** schema-driven adapter | No mandatory Elementary install; avoid bespoke interpretation of artifacts | Pin dbt schema versions and fixtures; optional Elementary table version |
| Athena execution metadata | AWS SDK/API (boto3, Athena GetQueryExecution etc.) | **Proposed:** official SDK | Prefer API over scraping or copying collectors | API fields, permissions, throttling, cost correlation limits |
| Billing ledger/reconciliation | FOCUS semantics; database transaction patterns | **Proposed:** original | Need revisions, idempotency, authoritative totals | Accounting policy, golden tests, candidate library review |
| Identity correlation | OpenLineage facets; OTel IDs; provider query IDs | **Proposed:** original | No universal cross-system identity protocol | Evidence hierarchy and ambiguity tests |
| Shared/idle allocation | OpenCost (K8s only); FinOps guidance | **Proposed:** original for Athena | OpenCost does not directly allocate Athena workloads | Algorithm alternatives, fairness, cost pool conservation |
| SQL lineage | SQLGlot | **Deferred:** dependency | Avoid writing SQL parser; inference is nonauthoritative | Dialect accuracy, licensing, pinned version |
| Metadata catalog | OpenMetadata / DataHub | **Deferred:** optional APIs | No required graph service | API compatibility and deployment overhead |
| Dataset contracts | ODCS | **Deferred:** optional reader | Avoid proprietary contract schema | Schema pin and interoperability tests |
| Telemetry | OTel SDK/exporters | **Deferred:** dependencies | No custom trace protocol | Semantic convention and privacy review |
| API contracts | FastAPI OpenAPI 3.1; JSON Schema | **Proposed:** generated schema | Avoid manual incompatible API description | Contract tests, pinned FastAPI/Pydantic versions |
| SBOM/license | CycloneDX or SPDX tooling; license scanner | **Proposed:** tooling | Trace transitive dependencies and vendored source | CI output and policy for unknown licenses |

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
