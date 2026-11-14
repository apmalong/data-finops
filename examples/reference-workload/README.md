# Reference workload

**Torn down on 2026-11-07** (#26). The record is in `docs/implementation/progress.md`. The configuration of the stack, `Pulumi.dev.yaml`, is kept, so the steps below can deploy it again.

This is a small AWS workload that you can remove. Its only purpose is to validate data-finops against real billing (ADR 0021). It has an S3 bucket, a Glue database and an Athena workgroup. It also has a tiny dbt-athena project in `dbt/` that runs in that workgroup. It is a separate uv project, and `data_finops` never imports it.

## Rules

- A `pulumi preview`, `pulumi up` or `pulumi destroy` against a real account is permitted only with the explicit approval of the account owner (`docs/implementation/execution-permissions.md`).
- Pulumi keeps the state locally, in the git-ignored `.local/` folder of the repository. Thus a Pulumi Cloud account is not necessary (ADR 0001). The program does not run if `PULUMI_BACKEND_URL` is not a `file://` URL.
- Deploy with a dedicated profile that has the least privilege. Keep it separate from each identity that reads billing data later (`execution-permissions.md`, the security section of AGENTS.md).
- The program creates each resource in us-east-1 through an explicit provider. This is the Region of the FOCUS export.
- Before you use real outputs as fixtures, anonymize them. Use one secret (`DATA_FINOPS_ANON_SECRET`), so that the pseudonyms are the same in all sources:
  - billing exports: `scripts/anonymize_focus_export.py`
  - Athena history, dbt artifacts and OpenLineage events: `scripts/anonymize_workload_outputs.py`. It keeps only the fields on its allowlists (`scripts/anonymize_workload_fields.py`), and it reports each field that it removes.

## Configuration (per installation)

| Key | Default | Meaning |
|---|---|---|
| `bytesScannedCutoffPerQuery` | 104857600 (100 MiB) | The Athena scan limit for each query in the workgroup |
| `budgetMonthlyUsd` | unset | An optional monthly AWS Budget amount. Set it together with `budgetEmail`. The budget covers **all** spend in the account, not only this workload. It sends an alert when the forecast spend or the actual spend gets to the amount. |
| `budgetEmail` | unset | Recipient of the budget alert |

## Commands

```powershell
cd examples/reference-workload
uv sync
uv run pytest                                   # offline, Pulumi mocks
uv run mypy
$env:PULUMI_BACKEND_URL = "file://$((Resolve-Path ../..).Path.Replace('\', '/'))/.local/pulumi"
$env:PULUMI_CONFIG_PASSPHRASE = "<choose one; needed for every later command>"
pulumi stack init dev                           # first time only
pulumi config set aws:region us-east-1
pulumi config set aws:profile <your profile>
pulumi preview                                  # owner approval required
pulumi up                                       # owner approval required
pulumi destroy                                  # owner approval required
```

## dbt project (`dbt/`)

The project has synthetic seeds (`raw_customers`, `raw_orders`), two staging views and two marts. `daily_orders` is incremental (append). Thus each run after the first issues more than one Athena query for this model.

### Query comment for data-finops correlation

The default query comment of dbt has `node_id` but not `invocation_id`. For data-finops to correlate a dbt-on-Athena project, add this to its `dbt_project.yml`. Also add the macro in `dbt/macros/data_finops_query_comment.sql`:

```yaml
query-comment:
  comment: "{{ data_finops_query_comment(node) }}"
  append: false
```

dbt-athena then sends each query as:

```sql
-- /* {"app": "dbt", "dbt_version": "1.12.6", "invocation_id": "<uuid>", "node_id": "model.reference_workload.daily_orders", "target_name": "reference"} */
<statement>
```

The adapter sends a statement that starts with `alter`, `drop`, `optimize`, `vacuum` or `msck` **without** a comment (OQ-22).

### OpenLineage

When you run `dbt-ol` (openlineage-dbt) from `dbt/`, it reads `dbt/openlineage.yml`. It adds events to `.local/openlineage/dbt-events.jsonl` at the root of the repository. openlineage-dbt requires `openlineage-sql`, which has no Windows wheel. Thus uv installs it only on Linux and macOS. Run `dbt-ol` in WSL or in a Linux container.

`dbt parse` and the offline tests work on Windows. If you use WSL on the same checkout, set `UV_PROJECT_ENVIRONMENT=.venv-linux`. Then uv does not build the Windows `.venv` again.

### Commands

```bash
cd examples/reference-workload
uv run pytest                                   # includes the offline dbt parse (no AWS)
# Real runs (#24, owner approval required), from WSL or Linux:
export REFERENCE_WORKLOAD_BUCKET=<bucket output of the stack>
export AWS_PROFILE=<your profile>
export UV_PROJECT_ENVIRONMENT=.venv-linux      # when sharing a Windows checkout
mkdir -p ../../.local/openlineage
cd dbt
uv run dbt-ol seed --profiles-dir .
uv run dbt-ol run --profiles-dir .
uv run dbt-ol run --profiles-dir .              # second run: daily_orders issues several queries
```

## Airflow (`airflow/`, #66)

This part runs the dbt project as a task of an Airflow 3 DAG. Then the OpenLineage events show how Airflow gives its run to dbt-ol (OQ-2). One container runs `airflow standalone` (SQLite, one process). The image is `apache/airflow:3.3.2-python3.12` (released on 2026-09-17), pinned by digest, with the OpenLineage provider that it bundles (2.20.1).

- **dbt in its own virtualenv.** The image installs dbt-core, dbt-athena and openlineage-dbt from `airflow/requirements-dbt.txt`. That file has hashes, and its versions are the versions in `uv.lock`. The task removes the `PYTHONPATH` of the image, because it puts the Airflow packages before the dbt packages. The task also puts the virtualenv first on `PATH`, because dbt-ol starts `dbt` by its name.
- **Parent run.** The DAG `reference_workload` has one task, `dbt_run`. This task gives the `lineage_parent_id` and `lineage_root_parent_id` macros of the provider to `dbt-ol run` as `OPENLINEAGE_PARENT_ID` and `OPENLINEAGE_ROOT_PARENT_ID`. Their format is `namespace/job/run_id`, which openlineage-dbt 1.54.0 reads. The OpenLineage namespace of Airflow is `reference-airflow`.
- **Events.** Airflow adds its events to `.local/openlineage/airflow-events.jsonl`. dbt-ol adds its events to `.local/openlineage/dbt-events.jsonl`. Both files are in the git-ignored `.local/` folder at the root of the repository.
- **Mounts.** The container mounts only `dbt/` (read-write, for `target/` and `logs/`), `.local/openlineage/` and the DAGs (read-only), not the whole checkout. The container must have write access to these mounts. On Docker Desktop, the `airflow` user of the image sees the bind mounts as owned by root, and uid 50000 gets "Permission denied". Thus on Docker Desktop, set `AIRFLOW_UID=0`. On Linux, set it to your own uid (`id -u`).
- **AWS.** `compose.yaml` gives no credentials. A real run adds `compose.aws.yaml`. That file gets the short-lived credentials of one profile from environment variables, and it does not mount an AWS folder. It requires the bucket of the stack.
- **DAG-run events.** `airflow dags test` emits the events of the task but not the START of the DAG run. Thus a real run goes through the scheduler (`up`, then trigger). Offline, that gives four events: DAG START, task START (with a ParentRunFacet that names the DAG run), task FAIL and DAG FAIL.
- **`exec` as root.** When the container runs as root, `docker compose exec` does not run the entrypoint that sets `PYTHONPATH`. Thus the commands go through `/entrypoint`. In Git Bash, set `MSYS_NO_PATHCONV=1`. Then Git Bash does not change `/entrypoint` into a Windows path.

Run these commands from `examples/reference-workload/airflow` in bash (Git Bash or WSL):

```bash
mkdir -p ../../../.local/openlineage
export AIRFLOW_UID=0                            # Docker Desktop; on Linux: $(id -u)
export MSYS_NO_PATHCONV=1                       # Git Bash only
export AIRFLOW_PORT=8080                         # UI on 127.0.0.1; change if 8080 is taken
docker compose build
# Offline check (no AWS): the DAG imports, and the task's parent variables render.
docker compose run --rm airflow bash -c "airflow db migrate && airflow dags list-import-errors \
  && airflow tasks render reference_workload dbt_run 2026-11-02T00:00:00+00:00"
# Real run (#75, owner approval required): one profile's short-lived credentials.
eval "$(aws configure export-credentials --profile <your profile> --format env)"
export REFERENCE_WORKLOAD_BUCKET=<bucket output of the stack>
docker compose -f compose.yaml -f compose.aws.yaml up -d
docker compose exec airflow /entrypoint airflow dags unpause reference_workload
docker compose exec airflow /entrypoint airflow dags trigger reference_workload
docker compose exec airflow /entrypoint airflow dags list-runs reference_workload  # until success
docker compose -f compose.yaml -f compose.aws.yaml down
```

After you change the pins, make `requirements-dbt.txt` again. Run these commands from `examples/reference-workload`:

```bash
uv export --frozen --no-dev --no-hashes --no-emit-project -o airflow/.lock-constraints.txt
uv pip compile airflow/requirements-dbt.in --universal --python-version 3.12 --generate-hashes \
  --constraints airflow/.lock-constraints.txt --no-annotate --no-header -o airflow/requirements-dbt.txt
rm airflow/.lock-constraints.txt
```
