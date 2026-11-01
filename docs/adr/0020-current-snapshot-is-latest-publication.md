# ADR 0020: The current Export Snapshot is the latest publication

Status: Accepted (2026-11-01, decided in #9). Refines ADR 0015 point 3; clarifies ADR 0019.

## Context
ADR 0015 makes the Export Snapshot ("all lines … as published at one time") the revision unit and says a restatement replaces it. It did not say which snapshot is current when several exist for one Snapshot Scope. Import order is not a safe answer: backfills, retries and delayed deliveries can import an older publication after a newer one, which would show a stale position (spec #6, story 7). ADR 0019 said a snapshot whose content is "already in the Ledger" is a replay; taken literally, content republished after a restatement (A, then B, then A again) would be dropped.

## Decision
1. The current snapshot for a Snapshot Scope is the one with the latest provider publication time (`published_at`), ties broken by the higher snapshot ID. It never depends on import order.
2. A replay is an import whose billing content equals the scope's **current** snapshot, or whose snapshot ID is already stored with the same content. Nothing is stored. (Clarifies ADR 0019's "already in the Ledger".)
3. An import with new content is stored. It is reported as new if it is now current, or as superseded if a later publication is current; superseded snapshots are kept as history.
4. A snapshot ID already stored with different content is rejected; stored snapshots are immutable.
5. `published_at` must come from the provider's own export metadata (for example the export manifest), never from file modification time or import time.

## Consequences
Restatement is deterministic in any import order. A late older publication whose content equals the current snapshot is a replay and is not stored, so that publication is not recorded as separate history; no money or line is lost. Readers (#11, #12) carry the responsibility for a trustworthy `published_at`. Ledger files created before this ADR are incompatible (new `published_at_us` column) and are disposable: none exist outside tests before M1 closes.

## Alternatives rejected
- Import order: a late or replayed older export would silently become current.
- Treating any previously seen content as a replay: drops genuine republications after a restatement.
