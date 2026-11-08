# Data FinOps — Claude Code implementation package

Status (2026-11-07): **M0–M6 complete.**
- **Built:**
  - a FOCUS billing Ledger, verified against a real AWS export;
  - workload metadata from OpenLineage, dbt and Athena, and query correlation;
  - a deterministic allocation engine, its Athena policies checked against the real export (#91);
  - a local API and dashboard;
  - read-only collection from AWS (`data-finops collect`);
  - an optional AWS Deployment in Pulumi, with a policy pack and owner-run deploy and teardown wizards (`infra/pulumi/aws/`). It has not been deployed to a real account yet.
- **Not built yet:** a PyPI package.

Version 0.1.

## Quick start
Run the offline demo, which needs no cloud account. You need Python 3.12, [uv](https://docs.astral.sh/uv/) and a source checkout:

```sh
git clone https://github.com/apmalong/data-finops.git
cd data-finops
uv sync --extra api --frozen
uv run data-finops demo        # open http://127.0.0.1:8000/
```

The tutorial [Your first allocation](https://apmalong.github.io/data-finops/tutorials/first-allocation.html) walks through what you see, one step and one command at a time. `uv run data-finops --help` lists the commands.

## Documentation
The documentation for people who run data-finops is at <https://apmalong.github.io/data-finops/>: tutorials, how-to guides, reference and explanation. It is published from `site/` on every push to `main` (ADR 0025, spec 07).

## Mission
Build a free, open-source, customer-managed, modular FinOps application for data engineering workloads. Attribute authoritative cloud charges to queries, runs, pipelines, models, datasets, data products, tenants, and downstream consumers without double counting. Deliver a small usable AWS Athena + Airflow/OpenLineage + dbt vertical slice first.

## Start here (contributors)
1. Read `AGENTS.md` and `docs/specs/01-product.md`.
2. Read every ADR under `docs/adr/` before changing architecture.
3. Read `docs/specs/02-data-contracts.md`, `03-attribution.md`, `04-integrations.md`, and `05-security-operations.md`.
4. Implement phases in `docs/implementation/plan.md` in order, using `docs/implementation/acceptance.md` as the definition of done.
5. Consult `docs/research/upstream-audit.md` before reusing third-party source.

## Non-goals for MVP
No production AWS account required for local demo. No automatic remediation, no LLM-based attribution, no mandatory Marquez/OpenCost/Grafana, no production secrets in fixtures, no assertion that time overlap proves ownership.

## Deliverables
Python package, local runnable API, synthetic end-to-end demo, deterministic ledger, tests, optional Pulumi AWS deployment, license/provenance records, operator documentation.

## Bootstrap (M-1)
Install Python 3.12 and `uv`. In a network-enabled environment run `uv lock`, inspect and commit `uv.lock`, then `uv sync --extra dev --frozen`. Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy src`. CI intentionally requires the lockfile. No cloud account is needed for M-1.

## Standards and reuse policy
See `docs/adr/0014-standards-first-and-build-vs-reuse.md`, `docs/research/standards-conformance-matrix.md`, and `docs/research/build-vs-reuse-register.md` before implementing integrations. The matrices distinguish intended compatibility from verified conformance.
