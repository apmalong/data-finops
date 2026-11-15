# Data FinOps — Claude Code implementation package

Status (2026-11-07): **M0–M6 complete.**
- **Built:**
  - a FOCUS billing Ledger, verified against a real AWS export
  - workload metadata from OpenLineage, dbt and Athena, and query correlation
  - a deterministic allocation engine, with its Athena policies checked against the real export (#91)
  - a local API and dashboard
  - read-only collection from AWS (`data-finops collect`)
  - an optional AWS Deployment in Pulumi, with a policy pack and owner-run deploy and teardown wizards (`infra/pulumi/aws/`). It is not deployed to a real account yet.
- **In progress, M7 (#220):** a public demo dashboard, Cost Dimensions, and a comparison with AWS's own cost tools. Spec 09 gives the design. It is not built yet.
- **Not built yet:** a PyPI package.

Version 0.1.

## Quick start
The offline demo does not use a cloud account. To run it, install Python 3.12 and [uv](https://docs.astral.sh/uv/). Then get a source checkout and start the demo:

```sh
git clone https://github.com/apmalong/data-finops.git
cd data-finops
uv sync --extra api --frozen
uv run data-finops demo        # open http://127.0.0.1:8000/
```

The tutorial [Your first allocation](https://apmalong.github.io/data-finops/tutorials/first-allocation.html) explains what you see, with one step and one command at a time. `uv run data-finops --help` lists the commands.

## Documentation
The documentation for people who run data-finops is at <https://apmalong.github.io/data-finops/>. It has tutorials, how-to guides, reference and explanation. After each push to `main`, a GitHub Actions workflow publishes it from `site/` (ADR 0025, spec 07).

## Mission
Build a free, open-source, customer-managed, modular FinOps application for data engineering workloads. Attribute authoritative cloud charges to queries, runs, pipelines, models, datasets, data products, tenants and downstream consumers. Count each charge one time only. First, deliver a small usable vertical slice: AWS Athena, Airflow and OpenLineage, and dbt.

## Start here (contributors)
1. Read `AGENTS.md` and `docs/specs/01-product.md`.
2. Before you change the architecture, read every ADR in `docs/adr/`.
3. Read `docs/specs/02-data-contracts.md`, `03-attribution.md`, `04-integrations.md` and `05-security-operations.md`.
4. Implement the phases in `docs/implementation/plan.md` in their order. `docs/implementation/acceptance.md` is the definition of done.
5. Before you reuse third-party source code, read `docs/research/upstream-audit.md`.

## Non-goals for MVP
- The local demo does not use a production AWS account.
- No automatic remediation.
- No attribution by an LLM.
- Marquez, OpenCost and Grafana are not mandatory.
- No production secrets in fixtures.
- The product does not say that time overlap proves ownership.

## Deliverables
- a Python package
- a local API that you can run
- a synthetic end-to-end demo
- a deterministic ledger
- tests
- an optional Pulumi deployment to AWS
- license and provenance records
- operator documentation

## Run the checks
Install Python 3.12 and `uv`. Then make the environment of each project from its committed lockfile:

```
uv sync --extra dev --extra api --extra aws --frozen
uv sync --frozen --directory examples/reference-workload
uv sync --frozen --directory infra/pulumi/aws/policy
uv sync --frozen --directory infra/pulumi/aws
```

The tests of the dashboard's static mode also need Node, which runs its script as a browser does (#228). A GitHub runner has it.

If you change the dependencies of a project, run `uv lock` in that project. Examine the new `uv.lock`. Then commit it.

Then run all the checks with one command:

```
python -m scripts.gates
```

The command stops at the first check that fails. At the end, it prints a block that starts with `## Gates`. To run the checks of some projects only, give their names, for example `python -m scripts.gates root policy`. CI runs the same command.

To check the documents against the Simplified Technical English rules, run `python -m scripts.ste_check`. The documents in `scripts/ste_converted.txt` are a gate: if one of them breaks a rule, the check fails. The check only shows the problems in the other documents.

Some tests of the policy pack and the stack use the Pulumi CLI 3.272.0. A cloud account is not necessary.

## Standards and reuse policy
Before you implement an integration, read `docs/adr/0014-standards-first-and-build-vs-reuse.md`, `docs/research/standards-conformance-matrix.md` and `docs/research/build-vs-reuse-register.md`. The matrices show the difference between intended compatibility and verified conformance.
