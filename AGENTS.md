# Agent instructions

You implement the Data FinOps project that this repository specifies. Treat the specifications and ADRs as authoritative. Do not expand the scope silently. If the implementation shows a contradiction, document an ADR amendment. Then continue with the smallest safe interpretation.

## Engineering rules
- Python 3.12+, typed code, `pytest`, `ruff` and `mypy` (or pyright). Calculations are deterministic, with `Decimal` for currency. Monetary arithmetic never uses floating point.
- Build an **offline synthetic end-to-end path first**. Never make unit or integration tests require real cloud credentials.
- The cloud bill is authoritative. Telemetry explains the allocation. Never invent exact query costs from duration or time overlap.
- Keep billed amounts, estimated allocations and potential savings separate. Keep the provenance and the method of each allocation.
- Reconcile for each source billing line or cost pool, currency, period and adjustment version. Prevent double counts on replay.
- By default, avoid the export of credentials, raw SQL with sensitive literals, customer data or tenant identifiers to an external service.
- Optional integrations must fail open. A telemetry outage must not fail production ETL.
- Copy third-party source only with a license check for each file and its original notices. Also record the source URL, the pinned commit, the modifications and a provenance entry.
- Prefer official APIs and SDK dependencies to copies of application internals.
- Do not claim that an upstream code audit is complete. The earlier evaluation was preliminary, and no exact SHAs or test executions were verified.
- Do not make automatic infrastructure changes from optimization suggestions.

## Development workflow
All development follows the engineering skills of Matt Pocock (`mattpocock-skills` plugin). Tasks are only in GitHub Issues (`docs/agents/issue-tracker.md`). `docs/implementation/progress.md` is the verification log for each milestone, not a task list.
1. **Design** (when terms, scope or architecture change): `grill-with-docs`. Update `GLOSSARY.md` and the ADRs during the session. Ask high-impact questions as "Work with AI" below says. Put open decisions in `docs/implementation/open-questions.md`.
2. **Spec:** take the next milestone in `docs/implementation/plan.md` that is not finished, and run `to-spec`. Make one spec issue for each milestone, labeled `ready-for-agent`, with test seams agreed with the owner.
3. **Tickets:** `to-tickets` splits the spec into tracer-bullet vertical slices. It publishes them as sub-issues of the spec, with native blocked-by links and the `ready-for-agent` label. Work the frontier: the tickets whose blockers are all closed.
4. **Build:** `implement`, one branch for each PR, which can hold more than one ticket ("Tickets in one PR" below).
   - Use `tdd` (red, then green) only at the seams agreed for that ticket.
   - Run type checks and single test files often.
   - At the end, run `python -m scripts.gates` (each gate, in each project). Then run `code-review` against `main`. Its reviewers are advisory (see "Work with AI").
   - Commit with `Closes #<ticket>`.
5. **Standards gate:** before each collector, parser, serializer, lineage model, telemetry format, allocation algorithm or integration, apply ADR 0014 (see the section below).
6. **Pull request:** the body follows the `pr` skill (Summary, Evidence, Merge Danger).
   - For test and check results, the Evidence section quotes the `## Gates` block that `python -m scripts.gates` printed, or links the CI run. Never write them by hand.
   - The body also has the review label from "Work with AI".
   - `main` is protected. A PR is required, and the `quality` and `infra` checks must pass.
   - CI runs on a PR and on a push to `main`, never on a push to another branch alone. A PR runs the checks that its paths need (`scripts/ci_changes.py`, #321):
     - Documents alone (`docs/`, `.claude/`, Markdown at the root): only `python -m scripts.gates --docs` and the secret scan.
     - Code, tests or the site: the root and reference-workload gates. The `infra` job runs only for `infra/`, `src/`, the lockfiles, `scripts/gates.py` or `.github/`.
     - An unknown path, or a push to `main`: every check.
7. **Documentation:** each ticket names the Diátaxis documentation type that it changes (tutorial, how-to, reference, explanation, or none) in a `## Documentation` section. Done includes that change (ADR 0025, spec 07). The code-review Spec reviewer checks it against the diff.
   - Write and edit documentation with the `simplified-technical-english` skill (`.claude/skills/`).
   - *Milestone D exception (owner, 2026-11-05):*
     - Content-only tickets (site pages) skip `tdd`, mutation checks and the Standards reviewer. Write each page. Then build and check it with `python -m scripts.site build` and `check`.
       - For a content-only ticket, run the page runner test of the page and the full suite one time. One reviewer checks the accuracy against the ADRs and specs, and against the rules of the `simplified-technical-english` skill.
     - Tooling tickets keep the full loop.
     - The tickets of D that are not done are grouped into PRs. Each PR closes each ticket in it, with a section for each ticket. The groups are in `docs/implementation/plan.md`, section D.
8. **Close a milestone** only with verification that you can run. Record the commands and the output in `progress.md`, and update the affected documents.
9. **Bugs and intake:** bugs use `diagnosing-bugs`. Issue intake uses `triage`, with the labels in `docs/agents/triage-labels.md`.

**Tickets in one PR** (owner, 2026-11-14). Put tickets in one PR only if all of these are true:
- They change the same module or the same documents.
- They have the same review: the owner must check all of them, or none of them ("Work with AI").
- The owner can read the combined diff in one session.

The PR body has one section for each ticket. The PR closes each ticket: "Closes #A, closes #B". `docs/implementation/plan.md` lists the PRs of each milestone and their tickets. Only one PR is open at a time.

## Work with AI (#246)
AI agents write, test and review most of this repository, and the repository splits money. These rules make sure that the owner checks the work that matters most. They come from James Bach and Michael Bolton, "How to Approach AI and Testing", and the answers of the owner on 2026-11-14.

**Work that the owner must check before the merge.** An agent can do all other work alone. The owner must check a PR that changes one of these four kinds of work:
- **Money logic:** how a Billing Line is split. The modules are `attribution`, `calculation`, `ledger`, `billing` and `providers.athena_billing`.
- **Test oracles:** a new or changed expected value in a test, for example a golden fixture or a hand-written amount.
- **Decisions:** a new or changed ADR, spec rule or GLOSSARY term.
- **Security and infrastructure:** IAM policies (the deployer policy too), bucket policies, a new dependency or a change to a CI workflow.

**The review label.** Each PR body has one of these two labels:
- `AI-reviewed only`: no person checked the PR. The agent writes this label.
- `owner-checked`: the owner checked the PR. Only the owner changes the label to this one.

A PR that changes one of the four kinds of work must not merge with `AI-reviewed only`.

**High-impact questions in design sessions.** A question is high-impact if it is about money, or if its answer is difficult to change later. Ask it with options only. Give the recommendation after the owner answers. Each decision log records the source of each decision: `Owner` or `Accepted recommendation`.

**Ask twice.** Before you give the owner a high-impact recommendation or a result about money, get a second answer. Ask a second agent the same question in different words. The second agent does not see the first answer. If the two answers are different, show both to the owner. If they agree, report only the first answer.

**Reviews.** The AI reviewers in `code-review` are advisory. A review result helps, but a clean review does not prove that the work is correct.

**Claims.** A claim that something passed or was checked must have evidence. The evidence is a command and its output, a test, or a cited source.

## Repository conventions
- `src/data_finops/{contracts,billing,normalization,ledger,identity,lineage,dbt,workload,attribution,calculation,collection,reporting,api,cli,providers,public_demo}`, plus `lambda_handlers.py` and `aws_clients.py` (the `aws` extra).
- `uv run python -m data_finops.schemas` generates the contracts into `schemas/`.
- `scripts/` holds operator tools and upstream checks that use the network. It never holds product code.
- `infra/pulumi/aws`: the Pulumi components of the Deployment and, in `policy/`, its policy pack. These are two more uv projects with their own checks, which run in the `infra` job of CI. Only the owner runs `infra/pulumi/aws/wizards/`. An agent never runs them.
- `tests/{unit,integration,contract}`
- `examples/demo`
- `site/`: the user documentation site, deployed as committed (ADR 0025). `scripts/site/` is its dev tooling, outside the wheel.
- `scripts/gates.py`: dev tooling that runs each quality gate.
- `scripts/ste_check.py`: dev tooling that checks documents against the Simplified Technical English rules. `scripts/ste_converted.txt` lists the documents that it gates.
- `examples/reference-workload`: a separate uv project (ADR 0021). `data_finops` never imports it.

## Security
- Use read-only IAM for ingestion, and separate deployment permissions.
- Get secrets from the runtime identity or a secret manager.
- Limit queries.
- By default, there is no public ingestion endpoint.
- Never interpolate untrusted metadata directly into SQL.

## Before M0: mandatory M-1 bootstrap
Read `docs/implementation/M-1-bootstrap.md`, `docs/implementation/golden-fixtures.md`, `docs/implementation/execution-permissions.md` and `docs/implementation/architecture-boundaries.md`. First, generate and commit a reviewed `uv.lock` with Python 3.12. Then run the quality gates. The current fixture tests check only arithmetic and structure. Do not treat them as an implementation.

## Standards-first implementation gate (ADR 0014)
Before you write a collector, parser, serializer, lineage model, telemetry format, allocation algorithm or new integration, examine `docs/research/standards-conformance-matrix.md` and `docs/research/build-vs-reuse-register.md`.
- Evaluate maintained standards and implementations, update the register, and record a reasoned reuse or build decision.
- Prefer official schemas, SDKs and APIs.
- Pin and test versions, and keep extensions.
- Describe conformance as complete only when it is verified.
- A new custom wire format or a mandatory third-party service requires an ADR amendment.

## Agent skills

### Issue tracker

Issues are in GitHub Issues for apmalong/data-finops (through `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the root of the repository. See `docs/agents/domain.md`.
