# Agent instructions

You are implementing the Data FinOps project specified in this repository. Treat specifications and ADRs as authoritative. Do not silently expand scope. If implementation reveals contradictions, document an ADR amendment and proceed with the smallest safe interpretation.

## Engineering rules
- Python 3.12+, typed code, `pytest`, `ruff`, `mypy` (or pyright), deterministic calculations using `Decimal` for currency. No floating-point monetary arithmetic.
- Build an **offline synthetic end-to-end path first**. Never require real cloud credentials for unit/integration tests.
- The cloud bill is authoritative; telemetry explains allocation. Never invent exact query costs from duration or time overlap.
- Separate billed amounts, estimated allocations, and potential savings. Preserve provenance and method for every allocation.
- Reconcile per source billing line/cost pool, currency, period and adjustment version. Prevent double-counting on replay.
- Avoid exporting credentials, raw SQL containing sensitive literals, customer data, or tenant identifiers to an external service by default.
- Optional integrations must fail open: telemetry outage must not fail production ETL.
- No third-party source copied without per-file license check, original notices, source URL, pinned commit, modifications, and provenance entry.
- Prefer official APIs and SDK dependencies over copying application internals.
- Do not claim an upstream code audit is complete; prior evaluation was preliminary and no exact SHAs or test executions were verified.
- Do not make automatic infrastructure changes from optimization suggestions.

## Development workflow
All development follows Matt Pocock's engineering skills (`mattpocock-skills` plugin). Tasks live only in GitHub Issues (`docs/agents/issue-tracker.md`); `docs/implementation/progress.md` is the per-milestone verification log, not a task list.
1. **Design** (when terms, scope or architecture change): `grill-with-docs`, updating `GLOSSARY.md` and ADRs inline. Ask high-impact questions as "Working with AI" below says. Unresolved decisions go to `docs/implementation/open-questions.md`.
2. **Spec:** take the next unfinished milestone in `docs/implementation/plan.md` and run `to-spec`: one spec issue per milestone, labelled `ready-for-agent`, with test seams agreed with the owner.
3. **Tickets:** `to-tickets` splits the spec into tracer-bullet vertical slices, published as sub-issues of the spec with native blocked-by links and the `ready-for-agent` label. Work the frontier: tickets whose blockers are all closed.
4. **Build:** `implement`, one branch for each PR, which can hold more than one ticket ("Tickets in one PR" below). Use `tdd` (red, then green) only at the seams agreed for that ticket. Run type checks and single test files often. At the end, run `python -m scripts.gates` (every gate, in every project). Then run `code-review` against `main`. Its reviewers are advisory (see "Working with AI"). Commit with `Closes #<ticket>`.
5. **Standards gate:** before any collector, parser, serializer, lineage model, telemetry format, allocation algorithm or integration, apply ADR 0014 (see the section below).
6. **Pull request:** body follows the `pr` skill (Summary, Evidence, Merge Danger). For test and check results, the Evidence section quotes the `## Gates` block that `python -m scripts.gates` printed, or links the CI run. Do not write these results by hand. The body also has the review label from "Working with AI". `main` is protected: a PR and a passing `quality` check are required.
7. **Documentation:** every ticket names the Diátaxis documentation type it changes (tutorial, how-to, reference, explanation, or none) in a `## Documentation` section, and done includes that change (ADR 0025, spec 07). The code-review Spec reviewer checks it against the diff.
   Write and edit documentation with the `simplified-technical-english` skill (`.claude/skills/`).
   *Milestone D exception (owner, 2026-11-05):*
   - Content-only tickets (site pages) skip `tdd`, mutation checks and the Standards reviewer. Each page is written, then built and checked with `python -m scripts.site build` and `check`; its page runner test and the full suite run once; one reviewer checks accuracy against the ADRs and specs and the writing rules.
   - Tooling tickets keep the full loop.
   - D's remaining tickets are grouped into PRs that each close every ticket they contain, with a section per ticket. The grouping is in `docs/implementation/plan.md`, section D.
8. **Close a milestone** only with runnable verification: record commands and output in `progress.md` and update affected docs.
9. Bugs use `diagnosing-bugs`; issue intake uses `triage` with the labels in `docs/agents/triage-labels.md`.

**Tickets in one PR** (owner, 2026-11-14). Put tickets in one PR only if all of these are true:
- They change the same module or the same documents.
- They have the same review: the owner must check all of them, or none of them ("Working with AI").
- The owner can read the combined diff in one session.

The PR body has one section for each ticket. The PR closes each ticket: "Closes #A, closes #B". `docs/implementation/plan.md` lists the PRs of each milestone and their tickets. Only one PR is open at a time.

## Working with AI (#246)
AI agents write, test and review most of this repository, and the repository splits money. These rules make sure that the owner checks the work that matters most. They come from James Bach and Michael Bolton, "How to Approach AI and Testing", and the owner's answers on 2026-11-14.

**Work that the owner must check before the merge.** An agent can do all other work alone. The owner must check a PR that changes one of these four kinds of work:
- **Money logic:** how a Billing Line is split. The modules are `attribution`, `calculation`, `ledger`, `billing` and `providers.athena_billing`.
- **Test oracles:** a new or changed expected value in a test, such as a golden fixture or a hand-written amount.
- **Decisions:** a new or changed ADR, spec rule or GLOSSARY term.
- **Security and infrastructure:** IAM policies (the deployer policy too), bucket policies, a new dependency or a change to a CI workflow.

**The review label.** Each PR body has one of these two labels:
- `AI-reviewed only`: no person checked the PR. The agent writes this label.
- `owner-checked`: the owner checked the PR. Only the owner changes the label to this one.

A PR that changes one of the four kinds of work must not merge with `AI-reviewed only`.

**High-impact questions in design sessions.** A question is high-impact if it is about money or if its answer is difficult to change later. Ask it with options only. Give the recommendation after the owner answers. Each decision log records the source of each decision: `Owner` or `Accepted recommendation`.

**Ask twice.** Before you give the owner a high-impact recommendation or a finding about money, get a second answer. Ask a second agent the same question in different words. The second agent does not see the first answer. If the two answers are different, show both to the owner. If they agree, do not report the second answer.

**Reviews.** The AI reviewers in `code-review` are advisory. A finding helps, but a clean review does not prove that the work is correct.

**Claims.** A claim that something passed or was checked must have evidence. The evidence is a command and its output, a test, or a cited source.

## Repository conventions
`src/data_finops/{contracts,billing,normalization,ledger,identity,lineage,dbt,workload,attribution,calculation,collection,reporting,api,cli,providers}` plus `lambda_handlers.py` and `aws_clients.py` (the `aws` extra); contracts are generated into `schemas/` by `uv run python -m data_finops.schemas`; `scripts/` holds operator tools and network-dependent upstream checks, never product code; `infra/pulumi/aws` (the Deployment's Pulumi components and, in `policy/`, its policy pack: two more uv projects with their own checks, run in CI's `infra` job; its `wizards/` are run by the owner only, never by an agent); `tests/{unit,integration,contract}`; `examples/demo`; `site/` (the user documentation site, deployed as committed; ADR 0025); `scripts/site/` (its dev tooling, outside the wheel); `scripts/gates.py` (dev tooling: runs every quality gate); `scripts/ste_check.py` (dev tooling: checks documents against the Simplified Technical English rules; `scripts/ste_converted.txt` lists the documents that it gates); `examples/reference-workload` (ADR 0021: separate uv project, never imported by `data_finops`).

## Security
Use read-only IAM for ingestion, separate deployment permissions, secrets from runtime identity/secret manager, bound query limits, no public ingestion endpoint by default, and no direct SQL interpolation of untrusted metadata.

## Before M0: mandatory M-1 bootstrap
Read `docs/implementation/M-1-bootstrap.md`, `docs/implementation/golden-fixtures.md`, `docs/implementation/execution-permissions.md`, and `docs/implementation/architecture-boundaries.md`. First generate and commit a reviewed `uv.lock` using Python 3.12, then run quality gates. The current fixture tests check arithmetic and structure only; do not treat them as an implementation.

## Standards-first implementation gate (ADR 0014)
Before writing a collector, parser, serializer, lineage model, telemetry format, allocation algorithm, or new integration, inspect `docs/research/standards-conformance-matrix.md` and `docs/research/build-vs-reuse-register.md`. Evaluate maintained standards and implementations, update the register, and record a reasoned reuse/build decision. Prefer official schemas, SDKs, and APIs. Pin and test versions; preserve extensions; never describe unverified conformance as complete. A new custom wire format or mandatory third-party service requires an ADR amendment.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for apmalong/data-finops (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
