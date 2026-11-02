# Architecture diagrams

What M0–M2 built, drawn from the code as it stands. Terms are the ones in [`GLOSSARY.md`](../../GLOSSARY.md); decisions are in [`docs/adr/`](../adr/). Anything not built yet is drawn dashed and grey, with the milestone that brings it.

| Diagram | Answers | Tool |
|---|---|---|
| [System context](#system-context) | What feeds data-finops, what it keeps, what comes next | draw.io |
| [Ingest flow](#ingest-flow) | What happens to each input, and what is refused | D2 |
| [Domain model](#domain-model) | How the glossary terms relate | Mermaid |
| [Event ingest sequence](#event-ingest-sequence) | One OpenLineage file, call by call | Mermaid |
| [Database](#database) | Every table and column in the shared SQLite file | D2, generated |

## System context

![System context](system-context.drawio.svg)

The product reads files today. It makes no AWS calls, and anonymized copies of real outputs become fixtures through `scripts/`. The Ledger is the only cost authority. Lineage and dbt records explain work and never carry money (ADR 0003).

## Ingest flow

![Ingest flow](ingest-flow.svg)

Each input is checked before anything is written.

- **Billing** is all or nothing per Export Snapshot: an invalid export raises a validation error, and a snapshot ID already stored with other content raises `SnapshotConflictError`. A restatement is a new snapshot. It becomes current and the earlier one is kept as history (ADR 0020).
- **OpenLineage** files are screened event by event, so one bad event does not stop the rest of the file. The exception is a JSON array that cannot be parsed at all, which is rejected as a whole.
- **dbt** artifacts are accepted or refused as whole files.

For OpenLineage and dbt, nothing is overwritten: a second, different copy is kept next to the first and flagged (ADR 0022).

## Domain model

```mermaid
classDiagram
    direction LR
    class ExportSnapshot["Export Snapshot"] {
        Snapshot Scope: account, Billing Period, source
        published at
    }
    class BillingLine["Billing Line"] {
        FOCUS 1.2 cost columns
    }
    class WorkloadRun["Workload Run"] {
        status: running | completed | failed | aborted | conflicting
        flags
    }
    class DataAsset["Data Asset"] {
        namespace + name
    }
    class LineageEdge["Lineage Edge"] {
        input | output
        provenance: event digest
    }
    class ModelExecution["Model Execution"] {
        node ID + invocation
        dbt status
    }
    class DbtNode["dbt Node"] {
        node ID + invocation
        materialization, depends on
    }
    class QueryExecution["Query Execution"] {
        execution ID, account, region
        contract only, not stored yet
    }
    class IdentityLink["Identity Link"] {
        <<M3, planned>>
        Evidence, policy version
    }
    class Allocation {
        <<M4, planned>>
    }

    ExportSnapshot "1" *-- "many" BillingLine
    WorkloadRun "0..1" <-- "many" WorkloadRun : parent / root
    WorkloadRun "1" --> "many" LineageEdge : reports
    LineageEdge "many" --> "1" DataAsset
    ModelExecution "many" --> "1" DbtNode : runs, same invocation
    IdentityLink ..> QueryExecution
    IdentityLink ..> ModelExecution
    IdentityLink ..> WorkloadRun
    Allocation ..> BillingLine : share of its Cost Pool
    Allocation ..> QueryExecution : Allocation Target
```

There are no links between billing records and workload records yet. Identity Links (M3) will connect a Query Execution to the Model Execution and Workload Run that sent it, using only explicit Evidence such as the dbt query comment, never time overlap. Allocation (M4) will assign shares of Billing Lines' Cost Pools to Query Executions. Model Executions and Workload Runs then receive cost only as Roll-ups over their Identity Links. dbt-ol reports each model as its own Workload Run as well, and M2 keeps that apart from the Model Execution that dbt's run results report (ADR 0022).

## Event ingest sequence

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Store as WorkloadStore
    participant Screen as lineage.validation
    participant DB as SQLite file

    Caller->>Store: ingest_events(path)
    Store->>Screen: screen_events(path)
    Screen-->>Store: valid RunEvents, rejected and unsupported by position
    Store->>DB: BEGIN IMMEDIATE (write lock before duplicate checks)
    loop each valid RunEvent
        Store->>Store: remove SQL query text and stack traces, eventTime to UTC, sha256
        Store->>DB: INSERT OR IGNORE event (run, type, time, digest)
        alt newly stored
            Store->>DB: insert its Lineage Edges (provenance = digest)
            opt other content stored for this key
                Store->>Store: count a changed copy
            end
        else same event already stored
            Store->>Store: count a duplicate
        end
    end
    Store->>DB: COMMIT
    Store-->>Caller: IngestResult (accepted, duplicates, changed copies, rejected, unsupported)
    Note over Caller,DB: workload_run() later derives status, times, parent and root from the stored events, so arrival order never matters
```

## Database

![Database](database.svg)

Generated from the schema: the Ledger and the workload store each create their tables in a temporary file, and the script reads them back from SQLite. The only declared reference is between Ledger tables. Workload records point to their source event or artifact by digest (provenance), not by foreign key.

## Regenerating

| File | After you change | Run |
|---|---|---|
| `database.d2`, `database.svg` | any table or column (a test fails until you do) | `uv run python scripts/database_diagram.py`, then `d2 --layout elk docs/architecture/database.d2 docs/architecture/database.svg` |
| `ingest-flow.svg` | `ingest-flow.d2` | `d2 --layout elk docs/architecture/ingest-flow.d2 docs/architecture/ingest-flow.svg` |
| `system-context.drawio.svg` | the system's inputs, packages or stores | open it in draw.io (desktop, or the VS Code draw.io extension) and save; the file stays an SVG |
| Mermaid blocks | the glossary or the ingest path | edit this file; GitHub draws them |

Tools: D2 0.9.0 (`winget install Terrastruct.D2`), draw.io desktop 32.6.1 (`winget install JGraph.Draw`) or the `hediet.vscode-drawio` extension. CI checks only `database.d2` against the schema, not whether the SVGs are up to date with their sources.
