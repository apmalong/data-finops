# The data-finops Deployment (Pulumi)

Pulumi Python components that deploy data-finops into one AWS account (spec 08, ADR 0026), and the policy pack that checks them (`policy/`). Two uv projects, each with its own environment: `pulumi-policy` 1.21.0 pins protobuf 5.

## Rules

- **Owner-only:** every `pulumi preview`, `pulumi up` and `pulumi destroy` against a real account needs the account owner's explicit approval (`docs/implementation/execution-permissions.md`). The owner runs them through the deploy wizard (spec 08, owner-only steps); an agent never does.
- **Local state:** state is kept locally under the repository's git-ignored `.local/`. The program refuses any `PULUMI_BACKEND_URL` that is not `file://` (ADR 0001).
- **Pinned CLI:** the Pulumi CLI is pinned to the SDK's release, 3.272.0. A test checks the installed CLI.

## Offline checks (what CI runs)

```bash
cd infra/pulumi/aws/policy && uv sync --frozen && uv run pytest
cd .. && uv sync --frozen && uv run pytest
```

The second runs the stack under Pulumi's mocks. It also runs `pulumi policy analyze` with the real pack over the resources the program declared, a compliant stack and deliberately non-compliant ones. Nothing reaches AWS.

## Settings

Stack configuration, `data-finops:*` and `aws:region`, is listed in spec 08's settings table. The site will generate its reference page (#190).
