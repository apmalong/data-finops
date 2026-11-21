# M9 decisions: the incremental Deployment (owner, 2026-11-21)

Code, specs, PRs and tickets cite these as "Q<n>". The numbers continue from M7's log (`m7-decisions.md`, Q165–Q173). The design issue is #318. ADR 0033 is written after this log, and it is authoritative. This log records only who decided what.

High-impact questions (money, or difficult to change later) were asked with options only (AGENTS.md, Work with AI). The owner answered first. Then the recommendation was given, after a second agent answered the same question in different words. For those questions, the owner's choice and the other options are recorded. The other questions were asked with a recommendation. The Source column says which.

## Measured (2026-11-21)
A copy of the owner's Deployment database, measured on the owner's laptop. The scripts and the full report stay outside the repository.

| What | Value |
|---|---|
| Database file | 378 MB. Billing Lines 82,158 rows, 318 MB (84%). Allocations 59,244 rows, 47 MB (12%). All other tables less than 2 MB |
| Lines that a current Export Snapshot holds | 11,354 of 82,158 (14%). One Snapshot Scope has 44 Export Snapshots, one for each delivery |
| One stored Billing Line | about 3.9 KB |
| Publisher, local | 3.8 s, 265 MB peak, 441 files (2.75 MB) |
| Publisher, on the Deployment (2026-11-19) | 37 s, 804 MB of 1,024 MB. The gap to the local run has no explanation yet |
| Parses of each current Allocation Snapshot in one publish | 8 (4 Cost Bases × 2 periods) |
| Calculation Run, all scopes | 1.7 s. A replay costs the same, because the digest is compared after the engine |

## Round 1: target and limits

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q174 | The scale target | 10× the stored data of today inside today's Lambda sizes, plus a check at 100× to find which limit breaks first. The other options were today with headroom, and an absolute figure such as 1 million Billing Lines a month for 3 years | Accepted recommendation | ADR 0033 |
| Q175 | Whether the constraints stay fixed | Yes. One writer, conditional writes, whole Demo Snapshots, no hosted database or compute that is always on, and the Lambda limits. A change to one of them needs its own ADR | Accepted recommendation | ADR 0033 |
| Q176 | Bigger Lambdas | Only as a temporary fix: the publisher can go to 2 GB, but the design must reach the Q174 target without more. The other options were never, and freely | Accepted recommendation | ADR 0033 |
| Q177 | How to measure | **Owner:** both. Local runs on generated data at 1×, 10× and 100× today, made with the Public Demo simulator, and one run on the owner's Deployment through a wizard step. The third option was the Deployment only | Owner | ADR 0033 |
| Q178 | Scope | All three functions (collector, publisher, API), each in the order of its measured cost, with tickets only where a measurement shows a problem. The other options were the publisher only, and a redesign of all three whatever they cost today | Accepted recommendation | ADR 0033 |

## Round 2: history, triggers, calculation

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q179 | Where superseded history lives | **Owner:** each superseded Export Snapshot moves to its own archive object in S3. The Database keeps a record of it (ID, content digest, row count, totals) and a pointer. The other options were to keep everything in the one file, and to delete the lines and rely on the raw deliveries in the export bucket. The recommendation after the answer was the same option: reports still read superseded lines for a named Calculation Run, and the import replay check reads only the snapshot record | Owner | ADR 0033 |
| Q180 | The collector upload | The collector does not write the file back when a run stored nothing (spec 10 already says that such a run stores nothing) | Accepted recommendation | ADR 0033 |
| Q181 | How the publisher skips | It keeps the S3 event. It computes a digest of its inputs (the Calculation Run of each period and a fingerprint of the workload data) and stops before it renders when the digest is the same as the one in `current.json`. A test covers each source that the renderer reads. The other options were a digest of the rendered files, and a schedule | Accepted recommendation | ADR 0033 |
| Q182 | Parses in one publish | Each Allocation Snapshot is parsed one time for each publish, in its own ticket. The rendered files must not change, so the current output is the oracle | Accepted recommendation | ticket |
| Q183 | Calculate only what changed | **Owner:** the Input digest is computed before the engine runs. A scope whose digest is the same as that of its current Allocation Snapshot is skipped. The check in `store_allocations` stays. The owner first chose reuse of Allocations for each line, and then changed to this option after the recommendation: a line's reference contains its Export Snapshot ID, and each delivery makes a new one, so reuse almost never applies. The third option was a full calculation on each run | Owner | ADR 0033 |
| Q184 | Phase timings | Permanent log lines with the time and the peak memory of each phase: download, open, parse, render and write for the publisher, and fetch, ingest, calculate and write for the collector. A wizard step reads them, and the owner runs it | Accepted recommendation | ADR 0033 |

## Round 3: the archive

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q185 | The archive format | **Owner:** a small SQLite file with the tables of the Database, read through the Ledger code. It is not a new wire format (ADR 0014). The other options were gzipped JSON Lines (a new format), and Parquet. The recommendation after the answer was the same option | Owner | ADR 0033 |
| Q186 | When the archive is written | In the Collection Run where a new Export Snapshot replaces an earlier one. First the archive object (an immutable key, `If-None-Match`), then the rows are deleted and the file is compacted (`VACUUM`), then the conditional write of the Database. `VACUUM` runs only in a run that archived | Accepted recommendation | ADR 0033 |
| Q187 | Which Allocation Snapshots move | Both kinds: those on a superseded Export Snapshot, and earlier runs on the current one. The record of each Allocation Snapshot stays in the Database | Accepted recommendation | ADR 0033 |
| Q188 | How long archives are kept | **Owner:** always, under their own prefix, with no expiry rule (a lower-cost storage class is permitted), plus a setting `archiveRetentionDays` (0, the default, is always). A request for an expired archive gets the answer "archived and expired", not 404. The owner first chose a fixed period. Then the owner changed to this option when shown that OQ-16 and spec 02 keep all earlier snapshots. The third option was until the Billing Period closes | Owner | ADR 0033 |
| Q189 | The history that is already stored | It moves through the same path. Each run archives a maximum number of Export Snapshots, set from the Q184 timings | Accepted recommendation | ADR 0033 |
| Q190 | The publisher's workload fingerprint | A revision counter that the collector increments on each write. The other options were a mark for each source in the demo scope, and a hash of the rows that the renderer reads. A move to marks for each source needs timings that show that unnecessary publishes cost too much | Accepted recommendation | ADR 0033 |
| Q191 | The ADR | One new ADR (0033) amends ADR 0026. Each PR that comes from it needs the `owner-checked` label | Accepted recommendation | ADR 0033 |
| Q192 | Compression and schema version | Each archive is gzipped. Its record in the Database has the schema version that wrote it. The Ledger migrates an earlier version or refuses it with a clear error, and never reads it incorrectly | Accepted recommendation | ADR 0033 |
| Q193 | Archive keys and growth | The key comes from the snapshot ID and the content digest, so a run that repeats after a 412 writes the same key. The collector logs the total size of the archive prefix on each run | Accepted recommendation | ADR 0033 |

## Deferred
Publish only the files that changed (#318, question 1). This waits for the Deployment run of Q177, which shows where the publisher's 37 s go.
