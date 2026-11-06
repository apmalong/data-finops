# M6 research: Pulumi components, policy packs and offline validation

Checked 2026-11-06 for M6 (AWS deployment). It assumes Pulumi Python components (ADR 0002), a local `file://` state backend with no Pulumi Cloud (ADR 0001), and the existing mock-tested stack in `examples/reference-workload/` (`pulumi==3.273.0`, `pulumi-aws==7.52.1`). The exit criterion is "preview policies validated", and tests must never touch a real account.

**Method.** Claims come from pulumi.com docs, PyPI/npm metadata, the GitHub API, the `pulumi/pulumi` and `pulumi/pulumi-policy` CHANGELOGs, and source read from the installed wheels. These are `pulumi_aws` 7.52.1 and `pulumi` 3.273.0 in `examples/reference-workload/.venv`, plus `pulumi_policy` 1.21.0, which was downloaded to the scratchpad.

Some claims are marked **[verified locally]**. Each of those was run offline in a scratch directory, outside the repo, against no account and no backend login:

- pytest with mocks, using the venv's python directly.
- `pulumi policy analyze --file` on a hand-written state file, with locally installed CLI v3.272.0.

`pulumi preview` and `pulumi up` were never run. **[unverified]** marks claims not confirmed from a primary source or by running them.

---

## TL;DR for the M6 design

1. **The policy pack needs its own virtualenv.** `pulumi-policy` 1.21.0 pins `protobuf~=5.29.5`, and `dbt-core==1.12.6` needs `protobuf>=6`. The reference-workload environment cannot hold both. Protobuf 6 support is in pulumi-policy HEAD but has not been released. A pack environment with `pulumi==3.273.0`, `pulumi-aws==7.52.1` and `pulumi-policy==1.21.0` resolves fine.
2. **Local policy packs need nothing from Pulumi Cloud.** You run them with `--policy-pack <dir>`. `pulumi policy publish`, `enable`, groups, issues and pre-built packs are paid Pulumi Cloud features. M6 does not need them.
3. **Validation can be fully offline, in three layers:**
   - (a) Unit-test each validate function by building `ResourceValidationArgs` / `StackValidationArgs` by hand.
   - (b) Run the policies in-process over the resources that `set_mocks` records. Mock inputs use the same camelCase keys the policies see.
   - (c) Run `pulumi policy analyze --file state.json --policy-pack <dir>`. It runs the real pack through the CLI without executing the program, calling providers or logging in to a backend. It exits non-zero on mandatory violations.

   All three were **[verified locally]**. A real `pulumi preview --policy-pack` belongs only in the deploy runbook.
4. **Never construct `PolicyPack(...)` in a module that tests import.** Its constructor starts a gRPC server and blocks forever. Keep policies in a plain module and build `PolicyPack` only in `__main__.py`.
5. **Plain in-repo Python `ComponentResource` classes are enough.** Packaging components with `PulumiPlugin.yaml` is overkill for a single-language project. Mocks do see both the component and its children **[verified locally]**.
6. **Every pulumi-aws resource M6 needs exists in 7.52.1.** That release is the pin, uploaded 2026-10-28. The latest is 7.54.0, uploaded 2026-11-06. `s3.Bucket` is the current resource, and `s3.BucketV2` still exists but is deprecated, with removal planned for v8.
7. **Keep Lambda packaging out of unit tests.** `pulumi.FileArchive` / `AssetArchive` paths are not read under mocks. A missing directory passes **[verified locally]**, so building the package needs its own test. The zip limits are 250 MB unzipped and 50 MB for direct upload. A container image (`image_uri` plus ECR plus `pulumi-docker-build`) is the fallback if `pyarrow` pushes the package past those limits.

---

## 1. CrossGuard policy packs in Python

### Package and compatibility

- **`pulumi-policy` latest is 1.21.0, uploaded 2026-05-20**, Apache-2.0 ([PyPI](https://pypi.org/pypi/pulumi-policy/json)). The previous release was 1.20.0, on 2025-10-23.
- `requires_dist` is `pulumi<4.0.0,>=3.204.0`, `protobuf~=5.29.5`, `grpcio~=1.71` and `setuptools>=61.0`. That means it works with pulumi 3.273.x.
- `uv pip compile` (py3.12) with `pulumi==3.273.0`, `pulumi-aws==7.52.1` and `pulumi-policy==1.21.0` resolves to protobuf 5.29.6 and grpcio 1.85.1 **[verified locally]**.
- **Conflict.** Add `dbt-core==1.12.6` and resolution fails: "pulumi-policy==1.21.0 depends on protobuf>=5.29.5,<5.30.dev0 and dbt-core==1.12.6 depends on protobuf>=6.0,<8.0" **[verified locally]**.
  - The current reference-workload venv has protobuf 6.33.6.
  - The [pulumi-policy CHANGELOG](https://github.com/pulumi/pulumi-policy/blob/master/CHANGELOG.md) lists "Python: Support protobuf v6.x" under **HEAD (Unreleased)**. 1.21.0 contains only Node.js fixes.
  - **Consequence:** the policy pack gets its own `requirements.txt` / venv, set in `PulumiPolicy.yaml` `runtime.options.virtualenv`. Do not add it to the stack's `pyproject.toml`.
- CLI note: the machine has Pulumi CLI **v3.272.0** installed, while the SDK pin is 3.273.0. The CLI's latest is 3.274.1, released 2026-11-05 ([pulumi CHANGELOG](https://github.com/pulumi/pulumi/blob/master/CHANGELOG.md)).

### API, from `pulumi_policy/policy.py` in 1.21.0

- `ResourceValidationPolicy(name, description, validate=fn | [fn], enforcement_level=None, config_schema=None, remediate=None, validate_remediate=None, display_name=None, severity=None, framework=None, tags=None, remediation_steps=None, url=None)`.
  - The validator signature is `fn(args: ResourceValidationArgs, report_violation: Callable[[str, Optional[str]], None]) -> None | Awaitable`.
  - `args` has `resource_type`, `props` (the resource's **inputs**, with camelCase keys), `urn`, `name`, `opts: PolicyResourceOptions`, `provider`, `stack_tags`, `get_config()` and `not_applicable(reason)`.
- `StackValidationPolicy(name, description, validate=fn, ...)`.
  - `args.resources` is a list of `PolicyResource` objects, each with `resource_type`, `props`, `urn`, `name`, `opts`, `provider`, `parent`, `dependencies` and `property_dependencies`.
  - The [authoring guide](https://www.pulumi.com/docs/discovery-governance/guides/write-a-policy-pack/) notes that values unknown until apply are still unknown when a stack policy inspects them during preview.
- `PolicyPack(name, policies, enforcement_level=None, initial_config=None, description, display_name, readme, provider, tags, repository)`.
  - **The constructor starts a gRPC server, prints its port and sleeps forever** (`while True: time.sleep(...)`). This is why tests must import policies from a module that does not build the pack.
  - A Python pack's version comes from `PulumiPolicy.yaml` `version:`. The SDK falls back to `0.0.1`.
- **Unknowns.** At runtime, `props` is wrapped in `unknown_checking_proxy`. Reading an unknown value raises `UnknownValueError`, which the analyzer turns into an **advisory** diagnostic, "can't run policy ... during preview". A mandatory policy therefore does **not** fail a preview when the value it checks is unknown. Prefer checking inputs set to literals, such as `authorizationType` and `retentionInDays`, not computed outputs.
- **There are no typed helpers in Python.** Python has nothing like the TS `validateResourceOfType`. Filter on `args.resource_type == "aws:lambda/functionUrl:FunctionUrl"` and read the camelCase dict keys.

### Enforcement levels

`EnforcementLevel.ADVISORY` (the pack default), `MANDATORY`, `REMEDIATE` and `DISABLED` are defined in `policy.py`. The [authoring guide](https://www.pulumi.com/docs/discovery-governance/guides/write-a-policy-pack/) says:

- advisory warns.
- mandatory blocks the update.
- remediate fixes violations automatically, for resource policies only. The guide calls it an Enterprise-edition feature.
- disabled skips the policy.

In the SDK, a remediate policy with no remediation is treated as mandatory. Whether `REMEDIATE` works in a purely local OSS run is **[unverified]**, and M6 should not rely on it.

### Running locally, the file:// backend and Pulumi Cloud

- Create a pack with `pulumi policy new aws-python`. `PulumiPolicy.yaml` looks like `runtime: {name: python, options: {virtualenv: .venv}}` plus `version:`. Run it with `pulumi preview --policy-pack <dir> [--policy-pack-config config.json]` ([authoring guide](https://www.pulumi.com/docs/discovery-governance/guides/write-a-policy-pack/)).
- The authoring guide lists Pulumi Cloud access as "(Optional) ... if you want to publish and centrally manage policy packs. Not required for local policy pack usage with open source Pulumi."
- The [policies FAQ](https://www.pulumi.com/docs/support/faq/policies/) says running a local pack with `--policy-pack` is free, provided the policy is on disk. Organization-wide enforcement and viewing results in Pulumi Cloud require paid plans.
- `pulumi policy publish` is described as "Publish a Policy Pack to the Pulumi Cloud" ([CLI ref](https://www.pulumi.com/docs/iac/cli/commands/pulumi_policy/)). The same applies to `enable`, `disable`, `list`, `rm` and `group`. Policy enforcement in Pulumi Cloud is "available in the Essentials, Pro, and Enterprise editions" ([policy-as-code overview](https://www.pulumi.com/docs/discovery-governance/concepts/policy-as-code/)). **M6 should use none of these.**
- The docs pages read here do not explicitly say "`--policy-pack` works with the file:// backend". The claim follows from the CLI design: the analyzer runs locally, and the FAQ says it needs only the pack on disk.
  - **[verified locally]** (partly): `pulumi policy analyze --file` ran a Python pack with `PULUMI_BACKEND_URL` unset and no `~/.pulumi/credentials.json`.
  - `preview --policy-pack` on a `file://` stack was not run, per this task's rules **[unverified by execution]**.
- `pulumi install` supports Python policy packs that use the uv toolchain (CHANGELOG, #24951). The exact `PulumiPolicy.yaml` toolchain keys are **[unverified]**.

---

## 2. Testing policies offline

### Does Pulumi ship a test harness?

**No.**

- `pulumi policy` subcommands are `analyze`, `compliance`, `disable`, `enable`, `exception`, `get`, `group`, `install`, `issue`, `list`, `new`, `publish`, `remove` and `validate-config` ([CLI ref](https://www.pulumi.com/docs/iac/cli/commands/pulumi_policy/)). None is a test command. `validate-config` checks pack configuration only.
- The [authoring guide](https://www.pulumi.com/docs/discovery-governance/guides/write-a-policy-pack/) recommends unit tests. Its Python pytest example builds `ResourceValidationArgs(resource_type=..., props={"storageEncrypted": True}, urn=..., name=..., opts=..., provider=...)` by hand and collects violations through a callback.
- pulumi-policy 1.21.0 re-exports `unknownCheckingProxy` "so policy-pack test helpers can match runtime behavior" for **Node.js only** (CHANGELOG #442). The Python SDK has `pulumi_policy.proxy.unknown_checking_proxy`, but it is not a public export.

### Three offline layers

**(a) Unit-test the validate functions** **[verified locally]**, 2 tests passing.

Build `ResourceValidationArgs(type, props, urn, name, PolicyResourceOptions(False, [], None, [], PolicyCustomTimeouts(0,0,0), []), None)` and call `policy.validate(args, report)`. Stack policies work the same way, with `StackValidationArgs([PolicyResource(type, props, urn, name, opts, None, None, [], {})])`.

**(b) Bridge mocks to policies, in-process** **[verified locally]**.

- `pulumi.runtime.MockResourceArgs.inputs` already uses engine-level camelCase keys, such as `forceDestroy` in the existing tests and `authorizationType` and `retentionInDays` in the probe. Those are exactly the keys policies read in `props`.
- Running the program under `set_mocks`, then building `PolicyResource`s from the recorded args and calling the stack/resource policies, produced the expected violation.
- Caveats:
  - `MockResourceArgs` has `typ`, `name`, `inputs`, `provider`, `resource_id`, `custom` and `transforms`, but **no `urn` or `parent`**.
  - Inputs set to `None` are dropped.
  - Numbers come back as floats, for example `retentionInDays: 14.0`.
  - With `set_mocks(preview=True)`, outputs the mock doesn't return arrive as `pulumi.output.Unknown`. In the probe, `FunctionUrl.functionName` was unknown because the mock returned no `name`.
  - This layer needs `pulumi-policy` in the test environment. With the protobuf conflict above, that means a separate test environment from the dbt one.

**(c) `pulumi policy analyze --file`** **[verified locally]** with CLI v3.272.0.

- It is documented as "Analyze existing resource state against one or more local policy packs ... without executing the Pulumi program or making provider calls". `--file` analyzes "a state file (as produced by `pulumi stack export`) ... without requiring a stack or backend login" ([CLI ref](https://www.pulumi.com/docs/iac/cli/commands/pulumi_policy_analyze/)).
- The command was added in v3.229.0 (2026-04-02), and `--file` in v3.249.0 (2026-07-01), #23664 ([CHANGELOG](https://github.com/pulumi/pulumi/blob/master/CHANGELOG.md)).
- The probe used a hand-written v3 deployment JSON containing a FunctionUrl with `authorizationType: NONE` and a LogGroup without retention. The CLI started the Python pack through its virtualenv and reported `[mandatory] lambda-url-iam` and `[advisory] log-retention`, then exited **1**.
- Changing only the `inputs` value to `AWS_IAM`, with outputs left at `NONE`, cleared the violation. Analyze evaluates **inputs**.
- A test could therefore emit such a state file from the `RecordingMocks` records and run the real pack through the CLI. This is the closest offline equivalent of "preview policies validated". It does not exercise provider `Check`, defaults or diffs.

### Can `pulumi preview` run without AWS credentials?

- The provider has `skip_credentials_validation` ("Skip the credentials validation via STS API"), `skip_requesting_account_id`, `skip_metadata_api_check` and `skip_region_validation`, plus static `access_key`/`secret_key` and per-service endpoint overrides. These are in `pulumi_aws/provider.py` 7.52.1 and on the [registry config page](https://www.pulumi.com/registry/packages/aws/installation-configuration/). The docs frame them as options for "AWS API implementations that do not have STS", meaning LocalStack-style endpoints.
- With fake static keys and all skip flags set, a create-only preview *may* complete, because planning creates mostly runs provider-side `Check`/`Diff`. **[unverified; not run]**.
- What would fail:
  - Any invoke or data source that calls AWS, such as `aws.get_caller_identity` or `aws.get_region` used for ARNs.
  - Refresh or diff of existing resources.
  - Anything that needs the account ID. `skip_requesting_account_id` leaves partition/account-dependent computed values empty **[unverified]**.
- The requests would still go to real AWS endpoints with invalid keys, which arguably "touches" AWS. **Do not use this as the test path.** Use layers (a) to (c), and keep real `pulumi preview --policy-pack` for the deploy runbook against the real account.

---

## 3. ComponentResource best practice

From [Build a Component](https://www.pulumi.com/docs/iac/guides/building-extending/components/build-a-component/):

- `super().__init__("<package-name>:index:<ClassName>", name, None, opts)`. The page says the format "must match *exactly*" and the `index` segment is required, for example `"data-finops:index:Collector"`. **Changing the type token later recreates the resources** unless you add aliases.
- Set `pulumi.ResourceOptions(parent=self)` on **every** child. Without a parent, children don't inherit providers passed through `providers`. For a single explicit `provider=` as in `infra.py`, set `parent=self`. The parent then propagates the provider, or you can pass `provider` explicitly.
- Call `self.register_outputs({...})` at the end of `__init__`. It "signals Pulumi that the component creation process has completed". It is documented as required when the component has outputs.
- Name children from the component name (`f"{name}-logs"`). Renaming an instance "won't propagate to its hard-coded children".
- Aliases ([docs](https://www.pulumi.com/docs/iac/concepts/resources/options/aliases/)) handle a change of name, type or parent: `ResourceOptions(aliases=[Alias(name=...)])`, `Alias(parent=...)` or `Alias(type_=...)`.
  - Aliases are inherited by children when a parent moves.
  - To wrap existing root-level resources in a new component, use `Alias(parent=pulumi.ROOT_STACK_RESOURCE)`, as documented in `pulumi/resource.py` 3.273.0.
  - Remove an alias once all stacks have migrated.
- **Mocks see component children** **[verified locally]**. A `ComponentResource` with one child produced two `new_resource` calls, `datafinops:index:Collector` (`custom=False`) and the child (`custom=True`).
  - Existing `MOCKS.of_type(...)` assertions therefore keep working when resources move into components.
  - Tests that count "all resources" must filter `custom`.
  - Parentage is not exposed on `MockResourceArgs`. Assert it through the child's `urn` output, which embeds the parent type, or skip it.
  - The [unit-testing docs](https://www.pulumi.com/docs/iac/guides/testing/unit/) have no component-specific API. They state "the mock server does not implement the full Pulumi engine" and "Lifecycle hooks and resource transforms are not executed in mock tests".
- **Component packages are overkill here.** The [components concept page](https://www.pulumi.com/docs/iac/concepts/components/) says "a component is not required to be part of a package", and in-repo components need no install step. Packaging (`PulumiPlugin.yaml` plus a provider-hosting entry file, consumed with `pulumi package add`) matters for cross-language reuse. ADR 0002 is Python-only, so plain classes are enough.

---

## 4. Open-source AWS policy sets

| Set | Language | License / status | Notes |
|---|---|---|---|
| AWSGuard `@pulumi/awsguard` ([repo](https://github.com/pulumi/pulumi-policy-aws)) | TypeScript | Apache-2.0. npm latest **0.4.0, 2022-09-22**. Repo not archived, last push 2026-02-28 (GitHub API) | README: "currently being previewed and is subject to breaking changes". Template `awsguard-typescript`, run with `--policy-pack`. |
| Compliance Ready Policies `@pulumi/aws-compliance-policies` ([repo](https://github.com/pulumi/compliance-policies)) | TypeScript | Apache-2.0. npm latest **0.0.23, 2025-02-14**. The [FAQ](https://www.pulumi.com/docs/support/faq/policies/) marks it **deprecated** | PCI DSS / ISO 27001 / CIS. JS/TS only, applicable to stacks in any language. |
| Pulumi Cloud pre-built packs | n/a (service) | Paid: "Pulumi Best Practices on the Essentials edition and above, and compliance-framework packs on the Enterprise edition" ([overview](https://www.pulumi.com/docs/discovery-governance/concepts/policy-as-code/)) | Needs Pulumi Cloud, so ruled out by ADR 0001. |

No Python AWS policy library from Pulumi was found **[searched, not exhaustive]**. A TS pack can run against a Python stack, but it brings Node.js into the toolchain, which conflicts with ADR 0002's spirit. **Recommendation:** write a small Python pack with project-specific rules, and cite AWSGuard rule names as inspiration only.

---

## 5. pulumi-aws v7 resources

The names below are confirmed in the installed **pulumi-aws 7.52.1** wheel, with type tokens taken from `@pulumi.type_token`. The latest on [PyPI](https://pypi.org/pypi/pulumi-aws/json) is 7.54.0 (2026-11-06), which needs `pulumi>=3.269.0`.

| Python class | Type token (what policies/mocks see) | Notes from source |
|---|---|---|
| `aws.lambda_.Function` | `aws:lambda/function:Function` | `code: pulumi.Archive` conflicts with `image_uri` and `s3_bucket`. `package_type` is `Zip` (default) or `Image`, and `handler` is required for Zip. Change triggers are `source_code_hash` and `code_sha256`. `role` is required. The runtime enum includes `python3.12`, `python3.13` and `python3.14`. |
| `aws.lambda_.FunctionUrl` | `aws:lambda/functionUrl:FunctionUrl` | `authorization_type` is required, `AWS_IAM` or `NONE`. `invoke_mode` is `BUFFERED` or `RESPONSE_STREAM`. |
| `aws.lambda_.Permission` | `aws:lambda/permission:Permission` | Has `function_url_auth_type`, and `invoked_via_function_url` for URL invoke grants. |
| `aws.scheduler.Schedule` | `aws:scheduler/schedule:Schedule` | Requires `flexible_time_window`, `schedule_expression` and `target` (`arn`, `role_arn`). Also has `ScheduleGroup`. |
| `aws.s3.Bucket` | `aws:s3/bucket:Bucket` | This is the current resource. v7 moved it to the upstream implementation and renamed `loggings` to `logging`; use `s3.BucketLogging`. The bucket location argument is `bucket_region`, while `region` is v7's per-resource region. `s3.BucketV2` (`aws:s3/bucketV2:BucketV2`) **still exists but warns** "s3.BucketV2 has been deprecated in favor of s3.Bucket". The V2 resources go away in v8 ([v7 migration guide](https://www.pulumi.com/registry/packages/aws/how-to-guides/7-0-migration/)). |
| `aws.budgets.Budget` | `aws:budgets/budget:Budget` | Already used in `infra.py`. Also has `BudgetAction`. |
| `aws.cloudwatch.LogGroup` | `aws:cloudwatch/logGroup:LogGroup` | `retention_in_days`. `0` means never expire, and the `DELIVERY` class forces 2. Create it explicitly as `/aws/lambda/<fn>` so retention is managed. |
| `aws.iam.Role`, `RolePolicy`, `Policy`, `RolePolicyAttachment`, `aws.iam.get_policy_document` | `aws:iam/role:Role`, etc. | On `Role`, the `inline_policy` and `managed_policy_arns` arguments are **deprecated**. Use `RolePolicy` / `RolePolicyAttachment`, plus `RolePoliciesExclusive` / `RolePolicyAttachmentsExclusive` for exclusive management. |
| `aws.ecr.Repository` | `aws:ecr/repository:Repository` | Only for the image path. |

v7 also makes provider `assumeRole` a list (`assumeRoles`), per the migration guide.

---

## 6. Packaging Lambda code from a Python project

- `pulumi.FileArchive(path)` takes a directory or a `.tar`, `.tar.gz` or `.zip`. `pulumi.AssetArchive({name: Asset | Archive})` composes archives in memory (`pulumi/asset.py` 3.273.0). Pass either as `Function(code=...)`.
- **Building the directory** ([AWS Lambda Python packaging](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)):
  - Install dependencies into a flat folder at the zip root with `pip install --platform manylinux_2_28_x86_64 --target=package --implementation cp --python-version 3.12 --only-binary=:all: ...`. Python 3.12+ runtimes run on AL2023 and take `manylinux_2_28` tags. Use `_aarch64` for arm64.
  - Do not ship `__pycache__`.
  - Files need 644 permissions and directories 755. Watch this when zipping on Windows.
  - Limits are **250 MB unzipped**, including layers, and **50 MB** for direct upload. Larger packages go through S3 (`s3_bucket`/`s3_key` with an `s3.BucketObjectv2`).
  - Pulumi's `code` uploads directly, so the 50 MB limit likely applies **[unverified for Pulumi specifically]**.
  - `pyarrow` (ADR 0018) is large, and whether data-finops plus its dependencies fit 250 MB is **[unverified, measure it]**.
- **Image alternative:**
  - `package_type="Image"` with `image_uri`, an `aws.ecr.Repository`, and `pulumi-docker-build` (latest 0.0.23, 2026-10-29, needs `pulumi>=3.231.0`, [PyPI](https://pypi.org/pypi/pulumi-docker-build/json)).
  - This needs Docker at deploy time, and the build/push needs registry credentials, so it is not offline.
  - The container image limit was not checked in this pass **[unverified]**.
- **Offline-test implications:**
  - Under mocks the archive is passed through as a `pulumi.asset.AssetArchive` object. **The path is never read**, and a non-existent directory passed **[verified locally]**.
  - Mock tests therefore prove wiring only: `handler`, `runtime`, `role` and the archive type.
  - Keep "build the package" as a plain Python function, such as `build_lambda_package(dest) -> Path`, with its own pytest that asserts the handler is at the zip root, there is no `__pycache__`, and the size is under the limit. Pulumi code then just wraps the result in `FileArchive`.
  - Pinning the build output also keeps `source_code_hash` deterministic.

---

## Sources (checked 2026-11-06)

- PyPI JSON: [pulumi-policy](https://pypi.org/pypi/pulumi-policy/json) 1.21.0, [pulumi](https://pypi.org/pypi/pulumi/json) 3.274.1 / 3.273.0, [pulumi-aws](https://pypi.org/pypi/pulumi-aws/json) 7.54.0 / 7.52.1, [pulumi-docker-build](https://pypi.org/pypi/pulumi-docker-build/json) 0.0.23.
- npm registry: `@pulumi/awsguard` 0.4.0, `@pulumi/aws-compliance-policies` 0.0.23.
- Source: `pulumi_policy/policy.py` (wheel 1.21.0), `pulumi_aws/**` (wheel 7.52.1), `pulumi/runtime/mocks.py`, `pulumi/asset.py` and `pulumi/resource.py` (wheel 3.273.0).
- [pulumi-policy CHANGELOG](https://github.com/pulumi/pulumi-policy/blob/master/CHANGELOG.md) (HEAD to 1.19.0) and [pulumi CHANGELOG](https://github.com/pulumi/pulumi/blob/master/CHANGELOG.md) (3.229.0, 3.249.0, 3.274.1).
- Docs: [Write a policy pack](https://www.pulumi.com/docs/discovery-governance/guides/write-a-policy-pack/), [Policies FAQ](https://www.pulumi.com/docs/support/faq/policies/), [Policy as code](https://www.pulumi.com/docs/discovery-governance/concepts/policy-as-code/), [`pulumi policy`](https://www.pulumi.com/docs/iac/cli/commands/pulumi_policy/), [`pulumi policy analyze`](https://www.pulumi.com/docs/iac/cli/commands/pulumi_policy_analyze/), [Unit testing](https://www.pulumi.com/docs/iac/guides/testing/unit/), [Build a Component](https://www.pulumi.com/docs/iac/guides/building-extending/components/build-a-component/), [Components](https://www.pulumi.com/docs/iac/concepts/components/), [Aliases](https://www.pulumi.com/docs/iac/concepts/resources/options/aliases/), [AWS provider config](https://www.pulumi.com/registry/packages/aws/installation-configuration/), [pulumi-aws v7 migration](https://www.pulumi.com/registry/packages/aws/how-to-guides/7-0-migration/).
- GitHub: [pulumi/pulumi-policy-aws](https://github.com/pulumi/pulumi-policy-aws), [pulumi/compliance-policies](https://github.com/pulumi/compliance-policies).
- AWS: [Lambda Python .zip packaging](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html).
