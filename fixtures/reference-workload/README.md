# Reference workload sample

Real outputs of the reference workload (ADR 0021) run on 2026-11-01 (#24), anonymized with `scripts/anonymize_workload_outputs.py` (#32) and added with the owner's approval (#35). They sit beside the hand-written fixtures, which cover cases this run did not (failed queries, Airflow parents).

| Path | Source |
|---|---|
| `athena/query-executions.json` | BatchGetQueryExecution for the workgroup's 20 queries (plain AWS response; readers take the account and region separately) |
| `dbt/1-seed/run_results.json`, `dbt/2-run/run_results.json` | dbt-core 1.12.6 run results of `dbt-ol seed` and the first `dbt-ol run` |
| `dbt/3-run/manifest.json`, `dbt/3-run/run_results.json` | manifest and run results of the second `dbt-ol run` (the first run's manifest differs only in run metadata; the seed's also in some node fields) |
| `openlineage/dbt-events.jsonl` | 22 events from openlineage-dbt 1.54.0's file transport, as written |

## What it shows

- 17 of 20 queries carry the dbt comment; the 3 without it are `alter table` statements dbt-athena sends uncommented (OQ-22).
- On the second run, `model.reference_workload.daily_orders` (incremental) issues 3 queries in one invocation.
- `dbt-ol seed` emits no `dbt_run` facet; both runs do, matching their run results.
- The dbt artifacts carry fields their published schemas do not list (OQ-23).

## Anonymization

Bucket names, account IDs and local paths are keyed pseudonyms (consistent with the FOCUS anonymizer); dbt `user_id` is cleared (null) and `project_id` pseudonymized; run-results `args` keep only flags, numbers and selection settings; all SQL is cut to a dbt comment and its leading keywords (`create table …`). Query execution IDs, the workgroup, `node_id` and `invocation_id` are unchanged so the sources still correlate. For this public copy, the account pseudonym was then replaced by the AWS documentation placeholder `111122223333`, and Athena timestamps were converted to UTC so they do not reveal the operator's time zone. Before committing, the files were scanned for the original identifiers and none were found.
