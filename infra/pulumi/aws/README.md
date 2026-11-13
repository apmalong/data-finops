# The data-finops Deployment (Pulumi)

Pulumi Python components that deploy data-finops into one AWS account (spec 08, ADR 0026), and the policy pack that checks them (`policy/`). Two uv projects, each with its own environment: `pulumi-policy` 1.21.0 pins protobuf 5.

## Rules

- **Owner-only:** every `pulumi preview`, `pulumi up` and `pulumi destroy` against a real account needs the account owner's explicit approval (`docs/implementation/execution-permissions.md`). The owner runs them through the deploy wizard (spec 08, owner-only steps); an agent never does.
- **Local state:** state is kept locally under the repository's git-ignored `.local/`. The program refuses any `PULUMI_BACKEND_URL` that is not `file://` (ADR 0001).
- **Pinned CLI:** the Pulumi CLI is pinned to the SDK's release, 3.272.0. A test checks the installed CLI against the release of the installed SDK. Another test checks that the stack, the policy pack, the reference workload, CI and the wizards name one release.

## Offline checks (what CI runs)

```bash
cd infra/pulumi/aws/policy && uv sync --frozen && uv run pytest
cd .. && uv sync --frozen && uv run pytest
```

The second runs the stack under Pulumi's mocks. It also runs `pulumi policy analyze` with the real pack over the resources the program declared, a compliant stack and deliberately non-compliant ones. Nothing reaches AWS. CI also sets `DATA_FINOPS_BUILD_PACKAGE=1`, which builds the real Lambda package from the checkout and checks it. That downloads the arm64 wheels from PyPI.

## The Lambda package

`pulumi up` needs the functions' package built first:

```bash
cd infra/pulumi/aws && uv run python -m deployment.package
```

This writes `build/data-finops-lambda.zip` (git-ignored). The program refuses to run without it, and uploads it to the Deployment's bucket. A changed zip redeploys both functions.

## The owner's wizards

`wizards/deploy-and-verify.sh` and `wizards/teardown.sh` walk the owner through deploying and verifying the Deployment, and through destroying it and the reference workload (#26). Only the owner runs them: agents never do. Each asks before every step that changes anything, and masks account IDs, buckets, profiles and the API's URL in what it shows and in its record under `.local/`. Both use `deployment/state_check.py` on the exported state: whether a destroy keeps exactly the bucket and its settings, and whether anything is left. `tests/test_wizards.py` runs each with every command stubbed.

The deployer's draft policy is `iam/deployer-policy.draft.json`, unvalidated. A test checks that it reaches every service the program declares a resource in.

## Settings

Stack configuration, `data-finops:*` and `aws:region`, and the stack's outputs are listed on the site's generated reference page, `site/reference/deployment-settings.html` (#190). It is rendered from `deployment/settings.py` (`CONFIG_KEYS`, `BOUNDS`, each field's default and docstring) and `OUTPUTS` in `deployment/program.py`, read without importing them. After changing either, run `python -m scripts.site build` from the repository root. The deploy, read and teardown steps are the site's how-tos.
