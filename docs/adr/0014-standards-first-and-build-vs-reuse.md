# ADR 0014 — Standards-first architecture and build-versus-reuse gate

Status: Accepted (design policy). Upstream versions are verified and pinned in `docs/research/build-vs-reuse-register.md`, which lists what is not verified yet.

## Context
Cloud billing, lineage, telemetry, API schemas, and metadata already have established specifications and maintained implementations. Bespoke substitutes introduce interoperability and maintenance costs. Standards alone do not solve cross-system workload identity or fair cost attribution.

## Decision
1. Prefer published standards and stable provider APIs for interchange. Use existing supported libraries/SDKs rather than copy implementation internals.
2. Before implementing any new collector, parser, serializer, lineage model, telemetry format, or allocation mechanism, update `docs/research/build-vs-reuse-register.md` with evaluated alternatives, license, version/commit, maintenance, runtime footprint, compatibility and test evidence. A proposed implementation cannot move to 'approved' without this review.
3. Adopt FOCUS for canonical cloud billing, OpenLineage for lineage events, OTel for optional trace correlation, JSON Schema for payload validation, OpenAPI 3.1 for HTTP contracts, SPDX/CycloneDX for provenance, and Pulumi Python for optional deployment. Use the FinOps Foundation Framework as vocabulary/guidance, not an executable specification.
4. Preserve provider source records, original event facets and extension fields when practical. Namespace custom fields; do not misrepresent extensions as standardized fields.
5. Keep integrations optional. Do not require Marquez, OpenMetadata, DataHub, OpenCost, Elementary, or a hosted telemetry stack to run the offline MVP.
6. Original code is justified for workload identity evidence, billing reconciliation, allocation ledger, and provider-specific gaps that lack a suitable maintained implementation. Allocation formulas and evidence must be explicit and reproducible.
7. Version pinning: record **verified** upstream versions, schema URLs, commits, licensing, and test results before release. Never fabricate version compatibility or source audit completion.

## Consequences
Adapters and conformance tests are first-class deliverables. Optional dependencies stay optional. Provider-specific capabilities can lag until they are verified. Standards conformance is tested, not inferred from the conventions for names.

## Alternatives rejected
- Reinvent all schemas: unnecessary compatibility burden.
- Deploy a full metadata/lineage platform by default: excessive customer-managed overhead.
- Vendor source by default: avoidable provenance and update burden.

## Acceptance
M-1/M0 must establish the conformance matrix and the reuse gate. M1/M2 must add tests based on the upstream schema of each implemented protocol. Unknown compatibility is recorded as "pending", not as "passed".
