# Reference workload

A small, removable AWS workload used only to validate data-finops against real billing (ADR 0021): an S3 bucket, a Glue database and an Athena workgroup, plus a tiny dbt-athena project in `dbt/` that runs in that workgroup. It is a separate uv project and is never imported by `data_finops`.

## Rules

- Every `pulumi preview`, `pulumi up` and `pulumi destroy` against a real account needs the account owner's explicit approval (`docs/implementation/execution-permissions.md`).
- State is kept locally under the repository's git-ignored `.local/`, so no Pulumi Cloud account is needed (ADR 0001). The program refuses to run unless `PULUMI_BACKEND_URL` is a `file://` URL.
- Deploy with a dedicated, least-privilege profile, separate from any identity that later reads billing data (`execution-permissions.md`, AGENTS.md security).
- Every resource is created in us-east-1 through an explicit provider, matching the FOCUS export.
- Real outputs are anonymized before they are used as fixtures, with one secret (`DATA_FINOPS_ANON_SECRET`) so pseudonyms match across sources: billing exports with `scripts/anonymize_focus_export.py`, Athena history, dbt artifacts and OpenLineage events with `scripts/anonymize_workload_outputs.py`, which keeps only allowlisted fields (`scripts/anonymize_workload_fields.py`) and reports every field it drops.

## Configuration (per installation)

| Key | Default | Meaning |
|---|---|---|
| `bytesScannedCutoffPerQuery` | 104857600 (100 MiB) | Athena per-query scan limit for the workgroup |
| `budgetMonthlyUsd` | unset | Optional monthly AWS Budget amount; set together with `budgetEmail`. The budget covers **all** spend in the account, not just this workload, and alerts on forecast and actual spend reaching it |
| `budgetEmail` | unset | Recipient of the budget alert |

## Commands

```powershell
cd examples/reference-workload
uv sync
uv run pytest                                   # offline, Pulumi mocks
uv run mypy
$env:PULUMI_BACKEND_URL = "file://$((Resolve-Path ../..).Path)/.local/pulumi"
$env:PULUMI_CONFIG_PASSPHRASE = "<choose one; needed for every later command>"
pulumi stack init dev                           # first time only
pulumi config set aws:region us-east-1
pulumi config set aws:profile <your profile>
pulumi preview                                  # owner approval required
pulumi up                                       # owner approval required
pulumi destroy                                  # owner approval required
```

## dbt project (`dbt/`)

Synthetic seeds (`raw_customers`, `raw_orders`), two staging views and two marts. `daily_orders` is incremental (append), so runs after the first issue several Athena queries for one model.

### Query comment for data-finops correlation

dbt's default query comment has `node_id` but not `invocation_id`. Any dbt-on-Athena project that data-finops should correlate needs this in `dbt_project.yml`, plus the macro in `dbt/macros/data_finops_query_comment.sql`:

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

Statements starting with `alter`, `drop`, `optimize`, `vacuum` or `msck` are sent **without** a comment by the adapter (OQ-22).

### OpenLineage

`dbt-ol` (openlineage-dbt) reads `dbt/openlineage.yml` when run from `dbt/` and appends events to `.local/openlineage/dbt-events.jsonl` at the repository root. openlineage-dbt needs `openlineage-sql`, which has no Windows wheel, so it is installed only on Linux and macOS: run `dbt-ol` in WSL or a Linux container. `dbt parse` and the offline tests work on Windows. In WSL on the same checkout, set `UV_PROJECT_ENVIRONMENT=.venv-linux` so uv does not rebuild the Windows `.venv`.

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

Runs the dbt project as a task of an Airflow 3 DAG, so the OpenLineage events show how Airflow hands its run to dbt-ol (OQ-2). One container runs `airflow standalone` (SQLite, one process) from `apache/airflow:3.3.2-python3.12`, pinned by digest, with the OpenLineage provider it bundles (2.20.1).

- **dbt in its own virtualenv.** The image installs dbt-core, dbt-athena and openlineage-dbt from `airflow/requirements-dbt.txt`. Those requirements are hashed and use the versions in `uv.lock`; regenerate them with `uv pip compile airflow/requirements-dbt.in --universal --python-version 3.12 --generate-hashes --constraints <(uv export --frozen --no-dev --no-hashes --no-emit-project) -o airflow/requirements-dbt.txt`. The task unsets the image's `PYTHONPATH`, which would otherwise put Airflow's packages ahead of dbt's, and puts the virtualenv first on `PATH`, because dbt-ol starts `dbt` by name.
- **Parent run.** The DAG `reference_workload` has one task, `dbt_run`. It passes the provider's `lineage_parent_id` and `lineage_root_parent_id` macros to `dbt-ol run` as `OPENLINEAGE_PARENT_ID` and `OPENLINEAGE_ROOT_PARENT_ID` (`namespace/job/run_id`, which openlineage-dbt 1.54.0 reads). Airflow's OpenLineage namespace is `reference-airflow`.
- **Events.** Airflow appends to `.local/openlineage/airflow-events.jsonl` and dbt-ol to `.local/openlineage/dbt-events.jsonl`, both at the repository root, which is git-ignored.
- **Writing the checkout.** The container mounts the repository at `/workspace` and must be able to write it (dbt's `target/`, `.local/`). Docker Desktop bind mounts appear root-owned inside the container, so set `AIRFLOW_UID=0` there. On Linux, set it to your own uid (`id -u`).
- **AWS.** `compose.yaml` mounts no credentials. A real run adds `compose.aws.yaml`, which mounts your AWS config folder read-only and selects a profile.

```bash
cd examples/reference-workload/airflow
mkdir -p ../../../.local/openlineage
export AIRFLOW_UID=0                            # Docker Desktop; on Linux: $(id -u)
docker compose build
# Offline check (no AWS): the DAG imports, and the task's parent variables render.
docker compose run --rm airflow bash -c "airflow db migrate && airflow dags list-import-errors \
  && airflow tasks render reference_workload dbt_run 2026-11-02T00:00:00+00:00"
# Real run (#75, owner approval required):
export REFERENCE_WORKLOAD_BUCKET=<bucket output of the stack> AWS_PROFILE=<your profile>
export AWS_CONFIG_DIR=<path to your .aws folder>
docker compose -f compose.yaml -f compose.aws.yaml run --rm airflow bash -c \
  "airflow db migrate && airflow dags test reference_workload"
```

`airflow dags test` runs the DAG once in the container, with the OpenLineage listener active, and needs no web server. `docker compose up` starts the full standalone (UI on `127.0.0.1:8080`; the admin password is printed in its log) if you prefer to trigger it there.

