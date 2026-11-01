# ADR 0019: No row deduplication within an Export Snapshot

Status: Accepted (2026-11-01). Amends ADR 0015 point 3. "Already in the Ledger" is clarified by ADR 0020: replay compares with the scope's current snapshot.

## Context
ADR 0015 allowed row-level deduplication within an Export Snapshot. FOCUS defines no row identifier, so two identical rows in one snapshot cannot be told apart from two genuine identical charges. Removing either would delete real money and break Reconciliation against the provider's totals.

## Decision
Every row in an Export Snapshot is a distinct Billing Line, identified by its snapshot and position, even when its content matches another row. Replay is detected only at the snapshot level: importing an Export Snapshot whose content is already in the Ledger changes nothing.

## Consequences
Ledger totals always equal the provider's snapshot totals. A provider that genuinely emits duplicate rows by mistake is reconciled to what it billed, which is the authoritative position under ADR 0003.

## Alternatives rejected
- Content-hash deduplication of rows: silently drops identical legitimate charges.
