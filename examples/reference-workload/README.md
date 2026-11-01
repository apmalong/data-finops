# Reference workload

A small, removable AWS workload used only to validate data-finops against real billing (ADR 0021): an S3 bucket, a Glue database and an Athena workgroup, plus (in #23) a tiny dbt-athena project. It is a separate uv project and is never imported by `data_finops`.

## Rules

- Every `pulumi preview`, `pulumi up` and `pulumi destroy` against a real account needs the account owner's explicit approval (`docs/implementation/execution-permissions.md`).
- State is kept locally under the repository's git-ignored `.local/`, so no Pulumi Cloud account is needed (ADR 0001).
- Real outputs are anonymized (`scripts/anonymize_focus_export.py`) before they are used as fixtures.

## Configuration (per installation)

| Key | Default | Meaning |
|---|---|---|
| `bytesScannedCutoffPerQuery` | 104857600 (100 MiB) | Athena per-query scan limit for the workgroup |
| `budgetMonthlyUsd` | unset | Optional monthly AWS Budget amount; set together with `budgetEmail` |
| `budgetEmail` | unset | Recipient of the budget alert |

## Commands

```powershell
cd examples/reference-workload
uv sync
uv run pytest                                   # offline, Pulumi mocks
$env:PULUMI_BACKEND_URL = "file://$((Resolve-Path ../..).Path)/.local/pulumi"
$env:PULUMI_CONFIG_PASSPHRASE = "<choose one; needed for every later command>"
pulumi stack init dev                           # first time only
pulumi config set aws:region us-east-1
pulumi config set aws:profile <your profile>
pulumi preview                                  # owner approval required
pulumi up                                       # owner approval required
pulumi destroy                                  # owner approval required
```
