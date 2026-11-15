# ADR 0020: The current Export Snapshot is the latest publication

Status: Accepted (2026-11-01, decided in #9), amended 2026-11-05 (#12). Refines ADR 0015 point 3. Clarifies ADR 0019.

## Context
ADR 0015 makes the Export Snapshot ("all lines … as published at one time") the revision unit and says a restatement replaces it. It did not say which snapshot is current when more than one exists for one Snapshot Scope. The import order is not a safe answer. Backfills, retries and delayed deliveries can import an older publication after a newer one. Then a stale position shows (spec #6, story 7).

ADR 0019 said that a snapshot whose content is "already in the Ledger" is a replay. If this is taken literally, content that is published again after a restatement (A, then B, then A again) is lost.

## Decision
1. The current snapshot for a Snapshot Scope is the one with the latest provider publication time (`published_at`), ties broken by the higher snapshot ID. It never depends on import order.
2. A replay is an import whose billing content equals the scope's **current** snapshot, or whose snapshot ID is already stored with the same content. Nothing is stored. (Clarifies ADR 0019's "already in the Ledger".)
3. An import with new content is stored. It is reported as new if it is now current, or as superseded if a later publication is current; superseded snapshots are kept as history.
4. A snapshot ID already stored with different content is rejected; stored snapshots are immutable.
5. `published_at` must come from the provider's own export metadata (for example the export manifest), never from file modification time or import time.

## Consequences
Restatement is deterministic in any import order. A late older publication whose content equals the current snapshot is a replay, and it is not stored. Thus that publication is not recorded as separate history. No money or line is lost.

The readers (#11, #12) are responsible for a trustworthy `published_at`. AWS does not publicly document the fields of the Data Exports manifest. Thus the Parquet reader (#11) takes the snapshot metadata from its caller. The manifest parser, and with it the guarantee on `published_at`, moves to #12, where a real manifest can be checked. Ledger files created before this ADR are incompatible (new `published_at_us` column), and you can delete them: none exist outside tests before M1 closes.

## Amendment (2026-11-05, #12): where AWS states `published_at`
The real AWS Data Exports deliveries (FOCUS 1.2, 2026-10 and 2026-11) show that neither manifest records a publication time:
- `<export>-Manifest.json` holds `executionId`, `exportArn`, `columns`, `dataFiles` (S3 URIs) and `additionalOutputFiles`.
- `<export>-Manifest-FOCUS.json` holds `DataGenerator`, `DataGeneratorVersion` and `Schema` (with `FocusVersion`).

AWS writes each delivery under `data/` and `metadata/billing_period=YYYY-MM/<CreatedAt>-<executionId>/`. Its `<CreatedAt>` equals, to the millisecond, the `CreatedAt` that the Data Exports ListExecutions API reports for that execution.

**Decision:** for an AWS Data Exports Delivery, `published_at` is that `<CreatedAt>`. It is read from the S3 folder that the `dataFiles` URIs of the manifest name, not from local paths. The reason is that the folder name holds colons, which Windows cannot store. Thus the local layout of a download is the choice of the operator. The execution ID of the URIs must equal the `executionId` of the manifest. The snapshot ID is `aws-focus-<billing account>-<YYYY-MM>-<executionId>`.

If two Deliveries were created in the same millisecond, the tie-break of point 1 orders them: the higher snapshot ID, thus the execution ID. This order is deterministic but arbitrary. AWS was not seen to do this. Deliveries for a period come hours apart.

This keeps point 5. The time is the time of AWS, written by AWS at delivery. It is never the file modification time or the import time. It also keeps the import offline: no API call, so no credentials. The `LastUpdatedAt` of the API (delivery finished) was rejected. It orders deliveries in the same way, but it requires the network.

## Alternatives rejected
- Import order: a late or replayed older export would silently become current.
- Treat all content seen before as a replay: this loses genuine republications after a restatement.
