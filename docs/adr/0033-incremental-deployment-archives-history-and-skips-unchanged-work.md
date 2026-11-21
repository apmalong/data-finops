# ADR 0033: The Deployment archives superseded history and skips work that did not change

Status: Accepted (design, 2026-11-21). Decides M9 (#318, Q174–Q193, `docs/implementation/m9-decisions.md`). Amends ADR 0026 (point 2 and the consequences). Keeps OQ-16 and spec 02: all earlier snapshots are kept.

## Context
Each function of a Deployment works on the full Database. The cost of a run grows with all stored data, not with what changed. On a copy of the owner's Database (378 MB), 84% of the file is Billing Lines, and only 14% of them belong to a current Export Snapshot. Each delivery adds a full Export Snapshot to the file.

A stored line is about 3.9 KB. At 10× today, the file is about 3.7 GB, near the 5 GB limit of one `PutObject`. At 1 million lines a month, one Export Snapshot is about 4 GB.

Nothing is skipped today. The collector writes the file back on each run, so the S3 event starts the publisher each time, and the publisher publishes each time. The collector runs the engine for each scope on each run, and compares the Input digest only after the engine. Each publish parses each current Allocation Snapshot 8 times.

## Decision
1. **The target.** 10× the stored data of today, inside today's Lambda sizes, with a check at 100× (Q174). The constraints of ADR 0026 stay (Q175). The publisher can go to 2 GB as a temporary fix, but not more (Q176).
2. **Archives.** When a new Export Snapshot replaces an earlier one, the collector moves the earlier one out of the Database (Q179, Q186, Q187):
   - One archive object for each superseded Export Snapshot. It holds its Billing Lines and the Allocations of each Calculation Run on it. Earlier runs on the current Export Snapshot are archived too.
   - The archive is a gzipped SQLite file with the tables of the Database, read through the Ledger code (Q185, Q192). It is not a new wire format.
   - The Database keeps the record of each Export Snapshot and Allocation Snapshot, with the content digest, the row count, the totals, the schema version and the archive key. The import replay check reads only this record.
   - The order: write the archive under an immutable key from the snapshot ID and the content digest, with `If-None-Match`. Then delete the rows, run `VACUUM`, and write the Database with `If-Match` (Q193). An archive without a Database write does no harm. A run that repeats writes the same key.
   - The stored history moves through the same path, with a maximum number of Export Snapshots for each run (Q189).
3. **Retention.** Archives are kept always, under their own prefix (Q188). No lifecycle rule of the bucket applies to that prefix, except an optional move to a lower-cost storage class. The setting `archiveRetentionDays` can expire them (0, the default, means always). A request for an expired archive gets "archived and expired", not 404.
4. **The collector skips.** It computes the Input digest before the engine, and skips a scope whose digest is the same as that of its current Allocation Snapshot (Q183). The check in `store_allocations` stays. When a run stores nothing, the collector does not write the file back (Q180). Each write increments a revision counter in the Database (Q190).
5. **The publisher skips.** It keeps the S3 event. Its input digest is the Calculation Run of each period plus the revision counter. When that digest is the same as the one in `current.json`, it stops before it renders (Q181).
6. **Measurement.** The publisher and the collector log the time and the peak memory of each phase, and the total size of the archive prefix (Q184, Q193). These log lines are permanent. Local runs measure 1×, 10× and 100× on generated data, and one run on the owner's Deployment confirms them (Q177).

## Consequences
- **The file stays small.** At 10× today, the current lines are about 0.5 GB, far from the 5 GB `PutObject` limit and the 10 GB `/tmp`.
- **Reports of superseded runs are slower.** A report for a superseded Calculation Run downloads its archive first. The API needs read access to the archive prefix.
- **A run that archives costs more.** `VACUUM` rewrites the file in the Lambda run.
- **Archive storage grows without a limit** when `archiveRetentionDays` is 0. The size log shows the growth.
- **The publisher digest is coarse.** Each stored change starts a publish, also a change that the Public Demo does not show. A digest that is more exact is a later step, only if timings show a cost.
- **An input added to the engine must also go into the Input digest**, or a skip gives a result that is not current. A test ties the two together.
- **Object Lock.** ADR 0032 turns Object Lock on for the bucket with no default retention. Archives get no retention, so `archiveRetentionDays` can delete them.

## Alternatives rejected
- **Keep all history in the one file** (Q179): at 10× today, it is near the `PutObject` limit, and each run moves all of it.
- **Delete superseded lines and rely on the export bucket** (Q179): reports read superseded lines for a named run. The customer controls that bucket, and a later parser version can give a different content digest.
- **Reuse Allocations for each line** (Q183): a line's reference contains its Export Snapshot ID, and each delivery makes a new one, so reuse almost never applies. A mixed Allocation Snapshot also needs its own rules for reproducibility.
- **JSON Lines or Parquet archives** (Q185): each needs a second read path, and JSON Lines is a new wire format (ADR 0014).
- **A fixed retention period** (Q188): OQ-16 and spec 02 keep all earlier snapshots.
- **A publisher on a schedule** (Q181): it publishes late after a change, or when nothing changed.

## Revisit triggers
- The Deployment run (Q177) shows that the publisher's time goes to its S3 writes. Then decide "publish only the files that changed" (#318, question 1, deferred).
- The current Export Snapshot alone comes near a Lambda limit (at about 1 million lines a month). Then split the Database.
- Archive storage costs more than the Deployment's cost target.
