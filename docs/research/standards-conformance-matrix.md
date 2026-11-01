# Standards conformance matrix

**Status:** planning baseline, not a certification or proof of implementation. Pin exact versions from authoritative upstream documentation in M-1; never silently assume the latest specification is compatible. Record upstream schema URI, checksum or tag, verified date, and tests when validated.

| Domain | Standard / upstream | MVP use | Conformance contract | Validation evidence / state |
|---|---|---|---|---|
| Billing | FinOps FOCUS (ADR 0015) | Canonical cost and usage record; FOCUS rows are the internal Billing Line, `x_` extensions retained; AWS native FOCUS export first | Choose a specific supported FOCUS release; enumerate required/nullable fields, charge categories, time, currency, billing vs effective cost and adjustments; preserve provider extensions | **Pending:** version, field mapping, upstream fixtures, reconciliation tests |
| Billing methodology | FinOps Foundation Framework | Terms: allocation, unit economics, optimization | Guidance only; no assertion of machine conformance | **Reference only** |
| Lineage | OpenLineage event spec | Job/run/dataset events, facets | Validate event schema against pinned spec; preserve unknown facets; deduplicate by event identity and semantics | **Pending:** schema pin and upstream fixture tests |
| Traces | OpenTelemetry + semantic conventions | Optional span/run/query correlation | Use standard trace/span IDs; custom attributes prefixed `data_finops.*`; avoid claiming a convention exists for unstandardized query identifiers | **Deferred:** verify semantic convention stability |
| Metrics | OpenMetrics / Prometheus exposition | Optional performance and SLO metrics | Use existing exporter/client; no custom metrics wire format | **Deferred** |
| Metadata catalog | OpenMetadata / DataHub APIs | Optional import of data products and ownership | Integrate via published APIs; no mandatory catalog | **Deferred** |
| SQL | SQLGlot | Optional query parsing and dependency hints | Import pinned package; dialect-specific tests; parsing is not proof of runtime query attribution | **Deferred** |
| Kubernetes | OpenCost | Optional K8s allocation | Consume API for existing deployments; do not assert it calculates Athena/dbt costs | **Deferred** |
| Contracts | Open Data Contract Standard (ODCS) | Optional dataset contract metadata | Select supported ODCS release, schema validation, preserve extensions | **Deferred** |
| HTTP API | OpenAPI 3.1 | Document FastAPI endpoints | Generate schema; contract tests; error responses and pagination documented | **M5** |
| Payloads | JSON Schema | Billing/allocation and ingested events | Declare JSON Schema dialect explicitly; generate from Pydantic models; validate with format assertion enabled; validate fixtures and invalid examples | **Partial:** local schemas exist, conformance tests pending |
| Infrastructure | Pulumi Python | Optional customer-owned deployment | Pin provider/plugin versions, previews and least privilege | **M6** |
| Provenance | SPDX / CycloneDX | SBOM and third-party notices | Generate SPDX or CycloneDX SBOM; scan license exceptions and preserve NOTICE | **M-1** |

## FOCUS accounting rules to settle before M1
- Define authoritative charge basis per cost pool (e.g. billed vs effective cost); **never add both**.
- Retain currency, billing period, charge period, adjustment/revision identity, pricing unit, provider and resource identity.
- Separate actual charged amounts from modeled workload allocations and estimated savings.
- Negative adjustments, credits, taxes, refunds, and non-usage charges require explicit inclusion/exclusion rules and reconciliation categories.
- Treat native provider FOCUS exports as preferred where supported; converter mappings require verified release-specific tests.

## Evidence checklist per implemented integration
1. Upstream source link and pinned version/tag/commit; license and any per-file notices.
2. Supported schema/API features and intentionally unsupported fields.
3. Upstream positive and negative test fixtures with licensing checked.
4. Local contract tests including unknown extensions, missing fields, duplicate/replayed events, and forward-compatibility behavior.
5. Verification date, command output, and maintainer/issue reference when deviations exist.

Update this table as each milestone is implemented. 'Pending' means not verified, not noncompliant.
