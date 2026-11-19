# ADR 0003: Authoritative billing ledger

Status: Accepted (design)

## Context
The FinOps tool must remain lightweight, auditable, portable and safe for customer-managed data platforms.

## Decision
Cloud billing records are the financial source of truth. Telemetry allocates rather than creates charges. Preserve source cost basis, credits, currencies and revisions.

## Consequences
Allocations can be estimates while totals remain reconciled.

## Amendment (2026-11-19, #212)
"Totals remain reconciled" was not checked when a bill was loaded. Only tests and `scripts/reconcile_delivery.py` reconciled. Now each load reconciles. `Ledger.ingest_focus` reads a FOCUS file, imports it and reconciles the stored snapshot against the file, in each Cost Basis. On a difference, the import rolls back, so the Ledger does not keep the snapshot.

`data-finops ingest focus` and each Collection Run load a bill through it (owner, Q92 and Q93). A replay stores nothing, so it has nothing to reconcile.

## Alternatives and revisit triggers
Revisit if measured integration cost, scale, license obligations, or customer requirements make this choice unsuitable. Record a new ADR that supersedes this one.
