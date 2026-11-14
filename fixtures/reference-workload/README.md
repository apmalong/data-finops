# Reference workload sample

These files are real outputs of a run of the reference workload (ADR 0021) on 2026-11-01 (#24). `scripts/anonymize_workload_outputs.py` anonymized them (#32, with the field allowlists of #36). The owner approved them before they were added (#35). They are next to the hand-written fixtures, which cover cases that this run did not have: failed queries and Airflow parents.

| Path | Source |
|---|---|
| `athena/query-executions.json` | BatchGetQueryExecution for the 20 queries of the workgroup. It is the AWS response as AWS sent it. The readers get the account and the Region separately. |
| `dbt/1-seed/run_results.json`, `dbt/2-run/run_results.json` | dbt-core 1.12.6 run results of `dbt-ol seed` and the first `dbt-ol run` |
| `dbt/3-run/manifest.json`, `dbt/3-run/run_results.json` | The manifest and run results of the second `dbt-ol run`. The manifest of the first run is different only in run metadata. The manifest of the seed is also different in some node fields. |
| `openlineage/dbt-events.jsonl` | 22 events from the file transport of openlineage-dbt 1.54.0, as it wrote them |
| `airflow/airflow-events.jsonl`, `airflow/dbt-events.jsonl`, `airflow/run_results.json` | A later `dbt-ol run` as a task of an Airflow 3.3.2 DAG (bundled OpenLineage provider 2.20.1), run on 2026-11-02 (#75). The files hold 4 events of Airflow (DAG and task, START and COMPLETE), 10 events of dbt-ol, and the run results of that invocation. |

## What it shows

- Under Airflow (`airflow/`, OQ-2), the invocation run of dbt-ol names the Airflow task run as its parent and the DAG run as its root. These run IDs are the same as in the Airflow events, because the DAG gives the provider's `lineage_parent_id` and `lineage_root_parent_id` macros to dbt-ol. The invocation run of dbt-ol uses the OpenLineage namespace of Airflow, `reference-airflow`. Its model runs keep the namespace `dbt`.
- 17 of 20 queries have the dbt comment. The other 3 are `alter table` statements that dbt-athena sends without a comment (OQ-22).
- On the second run, `model.reference_workload.daily_orders` (incremental) issues 3 queries in one invocation.
- `dbt-ol seed` emits no `dbt_run` facet. Both runs emit one, and it agrees with their run results.
- The dbt artifacts have fields that their published schemas do not list (OQ-23).

## Anonymization

The anonymizer made these changes:
- Bucket names, account IDs and local paths became keyed pseudonyms, made in the same way as the FOCUS anonymizer makes them.
- dbt `user_id` became empty (null), and `project_id` became a pseudonym.
- The run-results `args` keep only flags, numbers and selection settings.
- Only the dbt and OpenLineage fields on the allowlists stay.
- Free text (descriptions, messages, doc blocks, tag values) became pseudonyms.
- Free-form maps (`meta`, `grants`, `table_properties`) became empty.
- All SQL became a dbt comment and its first keywords (`create table …`).

Query execution IDs, the workgroup, `node_id` and `invocation_id` did not change, so the sources still correlate.

Then, for this public copy, the account pseudonym became the AWS documentation placeholder `111122223333`. The Athena timestamps became UTC, so they do not show the time zone of the operator. Before the commit, a scan looked for the original identifiers in the files and found none.

The `airflow/` files got the same anonymization, with the facets of the Airflow provider added to the allowlists (#76):
- These fields became pseudonyms: owners, the users that triggered a run, DAG file locations, tags (job-tag keys too, which the provider sets to the tag), task code, inlets and outlets.
- UI fields and `unknownSourceAttribute` were removed.
- The account pseudonym became `111122223333`.

A different secret made the pseudonyms of the `airflow/` files. So pseudonyms such as `project_id` are different in the two sets. The `airflow/` files have no bucket names.
