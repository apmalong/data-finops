# The data-finops Deployment (Pulumi)

This folder holds the Pulumi Python components that deploy data-finops into one AWS account (spec 08, ADR 0026). It also holds the policy pack that checks them (`policy/`). They are two uv projects, and each has its own environment, because `pulumi-policy` 1.21.0 pins protobuf 5.

## Rules

- **Owner-only:** a `pulumi preview`, `pulumi up` or `pulumi destroy` against a real account is permitted only with the explicit approval of the account owner (`docs/implementation/execution-permissions.md`). The owner runs these commands through the deploy wizard (spec 08, owner-only steps). An agent never runs them.
- **Local state:** Pulumi keeps the state locally, in the git-ignored `.local/` folder of the repository. The program refuses a `PULUMI_BACKEND_URL` that is not `file://` (ADR 0001).
- **Pinned CLI:** the Pulumi CLI release is the release of the SDK, 3.272.0. A test compares the installed CLI with the release of the installed SDK. Another test makes sure that the stack, the policy pack, the reference workload, CI and the wizards name one release.

## Offline checks (what CI runs)

```bash
cd infra/pulumi/aws/policy && uv sync --frozen && uv run pytest
cd .. && uv sync --frozen && uv run pytest
```

The second command runs the stack under the Pulumi mocks. It also runs `pulumi policy analyze` with the real pack. The pack checks three inputs: the resources that the program declared, a compliant stack, and stacks that are not compliant on purpose. Nothing goes to AWS.

CI also sets `DATA_FINOPS_BUILD_PACKAGE=1`. Then a test builds the real Lambda package from the checkout and checks it. The build downloads from PyPI the arm64 wheels of the versions in `uv.lock`. The check makes sure that each version in the package is the locked version.

## The Lambda package

Before you run `pulumi up`, build the package of the functions:

```bash
cd infra/pulumi/aws && uv run python -m deployment.package
```

This command writes `build/data-finops-lambda.zip` (git-ignored). Without this file, the program does not run. The program uploads the file to the bucket of the Deployment. If the zip changes, Pulumi deploys both functions again.

## The owner's wizards

`wizards/deploy-and-verify.sh` and `wizards/teardown.sh` are the owner's wizards (#26). The first deploys the Deployment and verifies it. The second destroys the Deployment and the reference workload. Only the owner runs them. Agents never run them.

Before each step that changes something, a wizard asks the owner. A wizard masks account IDs, buckets, profiles and the URL of the API in what it shows and in its record in `.local/`. Both wizards use `deployment/state_check.py` on the exported state. It checks that a destroy keeps the bucket and its settings and nothing else. It also checks whether something is left. `tests/test_wizards.py` runs each wizard with a stub for each command.

The draft policy of the deployer is `iam/deployer-policy.draft.json`. It is not validated. A test makes sure that the policy gives access to each service in which the program declares a resource.

## Settings

The site's generated reference page, `site/reference/deployment-settings.html` (#190), lists the stack configuration (`data-finops:*` and `aws:region`) and the outputs of the stack. The site build makes this page from two sources: `deployment/settings.py` (`CONFIG_KEYS`, `BOUNDS`, and the default and docstring of each field) and `OUTPUTS` in `deployment/program.py`. The build reads these files but does not import them. After you change one of them, run `python -m scripts.site build` from the root of the repository. The site's how-to guides give the deploy, read and teardown steps.
