# Reference workload

A small, removable AWS workload used only to validate data-finops against real billing (ADR 0021): an S3 bucket, a Glue database and an Athena workgroup, plus a tiny dbt-athena project in `dbt/` that runs in that workgroup. It is a separate uv project and is never imported by `data_finops`.

## Rules

- Every `pulumi preview`, `pulumi up` and `pulumi destroy` against a real account needs the account owner's explicit approval (`docs/implementation/execution-permissions.md`).
- State is kept locally under the repository's git-ignored `.local/`, so no Pulumi Cloud account is needed (ADR 0001). The program refuses to run unless `PULUMI_BACKEND_URL` is a `file://` URL.
- Deploy with a dedicated, least-privilege profile, separate from any identity that later reads billing data (`execution-permissions.md`, AGENTS.md security).
- Every resource is created in us-east-1 through an explicit provider, matching the FOCUS export.
- Real outputs are anonymized before they are used as fixtures, with one secret (`DATA_FINOPS_ANON_SECRET`) so pseudonyms match across sources: billing exports with `scripts/anonymize_focus_export.py`, Athena history, dbt artifacts and OpenLineage events with `scripts/anonymize_workload_outputs.py`.

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
