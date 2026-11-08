# ADR 0004: OpenLineage protocol and optional OTel

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Ingest OpenLineage events via lightweight receiver and use official SDKs. OTel is optional execution correlation. No mandatory Marquez/Tempo.

## Consequences
Maintain compatibility with existing lineage/observability stacks.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a superseding ADR.

## Note (drift review #207, 2026-11-07)
A Deployment does not mount the receiver: it pulls OpenLineage event files from an S3 Inbox (ADR 0027). The receiver stays a local and self-hosted feature (ADR 0024).
