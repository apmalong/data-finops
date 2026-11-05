# ADR 0020: The current Export Snapshot is the latest publication

Status: Accepted (2026-11-01, decided in #9); amended 2026-11-05 (#12). Refines ADR 0015 point 3; clarifies ADR 0019.

## Context
ADR 0015 makes the Export Snapshot ("all lines … as published at one time") the revision unit and says a restatement replaces it. It did not say which snapshot is current when several exist for one Snapshot Scope. Import order is not a safe answer: backfills, retries and delayed deliveries can import an older publication after a newer one, which would show a stale position (spec #6, story 7). ADR 0019 said a snapshot whose content is "already in the Ledger" is a replay; taken literally, content republished after a restatement (A, then B, then A again) would be dropped.

## Decision
1. The current snapshot for a Snapshot Scope is the one with the latest provider publication time (`published_at`), ties broken by the higher snapshot ID. It never depends on import order.
2. A replay is an import whose billing content equals the scope's **current** snapshot, or whose snapshot ID is already stored with the same content. Nothing is stored. (Clarifies ADR 0019's "already in the Ledger".)
3. An import with new content is stored. It is reported as new if it is now current, or as superseded if a later publication is current; superseded snapshots are kept as history.
4. A snapshot ID already stored with different content is rejected; stored snapshots are immutable.
5. `published_at` must come from the provider's own export metadata (for example the export manifest), never from file modification time or import time.

## Consequences
Restatement is deterministic in any import order. A late older publication whose content equals the current snapshot is a replay and is not stored, so that publication is not recorded as separate history; no money or line is lost. Readers (#11, #12) carry the responsibility for a trustworthy `published_at`. Because AWS does not publicly document the Data Exports manifest fields, the Parquet reader (#11) takes the snapshot metadata from its caller; parsing the manifest, and with it the guarantee on `published_at`, moves to #12 where a real manifest can be checked. Ledger files created before this ADR are incompatible (new `published_at_us` column) and are disposable: none exist outside tests before M1 closes.

## Amendment (2026-11-05, #12): where AWS states `published_at`
The real AWS Data Exports deliveries (FOCUS 1.2, 2026-10 and 2026-11) show that neither manifest records a publication time:
- `<export>-Manifest.json` holds `executionId`, `exportArn`, `columns`, `dataFiles` (S3 URIs) and `additionalOutputFiles`;
- `<export>-Manifest-FOCUS.json` holds `DataGenerator`, `DataGeneratorVersion` and `Schema` (with `FocusVersion`).

AWS writes each delivery under `data/` and `metadata/billing_period=YYYY-MM/<CreatedAt>-<executionId>/`. Its `<CreatedAt>` equals, to the millisecond, the `CreatedAt` that the Data Exports ListExecutions API reports for that execution.

**Decision:** for an AWS Data Exports Delivery, `published_at` is that `<CreatedAt>`. It is read from the S3 folder named in the manifest's own `dataFiles` URIs, not from local paths: the folder name holds colons, which Windows cannot store, so a download's local layout is the operator's. The URIs' execution ID must equal the manifest's `executionId`. The snapshot ID is `aws-focus-<billing account>-<YYYY-MM>-<executionId>`. Two Deliveries created in the same millisecond would be ordered by point 1's tie-break, the higher snapshot ID: by execution ID, which is deterministic but arbitrary. AWS has not been seen to do this; Deliveries for a period come hours apart.

This keeps point 5: the time is AWS's own, written by AWS at delivery, and never file modification time or import time. It also keeps import offline: no API call, so no credentials. The API's `LastUpdatedAt` (delivery finished) was rejected; it orders deliveries the same way but needs the network.

## Alternatives rejected
- Import order: a late or replayed older export would silently become current.
- Treating any previously seen content as a replay: drops genuine republications after a restatement.
