# M8 decisions: the control plane (owner, 2026-11-16)

Code, specs, PRs and tickets cite these as "Q<n>". The numbers continue from M7's log (`m7-decisions.md`, Q53–Q100). In the design session, the questions were numbered Q1–Q54. Here, each one is Q(100 + n), so session Q1 is Q101.

High-impact questions (money, security, or hard to change later) were asked with options only, so no recommendation was given (AGENTS.md, Work with AI). For those, the owner's choice and the other options are recorded. The other questions were asked with a recommendation, and the owner accepted each one. The Source column says which. Spec 10 and the control-plane ADR are written after this log, and they are authoritative. This log records only who decided what.

The owner's brief (2026-11-16): "i guess we need to think about what the control plane should actually look like". Today the CLI only writes, the API only reads, and a Deployment is controlled by Pulumi config, the wizards and a schedule.

## Round 1: who and what

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q101 | Who the control plane is for | **Owner:** a platform or data team that operates it, plus consumers who make some changes themselves without AWS access. The other options were one operator who owns the AWS account, and an operating team with read-only consumers | Owner | spec 10 |
| Q102 | Which jobs are in scope | **Owner:** all six: run on demand, run status, Inbox management, configuration that changes often without `pulumi up`, CLI reads without a server, and corrections to results | Owner | spec 10 |
| Q103 | Whether anything can be written over the network | **Owner:** control endpoints on the API, IAM-signed, with an operator policy next to the reader policy. The other options were a read-only API with AWS's own APIs for control, and a separate control function | Owner | control-plane ADR |
| Q104 | Whether local use and a Deployment work the same way | **Owner:** one CLI with a target, `--db` or `--deployment`. Each command does the same job in both places. The other options were two different surfaces, and shared reads only | Owner | spec 10 |

## Round 2: identity, configuration, corrections

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q105 | What identity consumers use | **Owner:** an OIDC sign-in in front of the API, with JWTs. Operators keep IAM. The other options were IAM Identity Center, the dashboard only, and pull requests to a configuration repository | Owner | control-plane ADR |
| Q106 | How permissions are scoped | **Owner:** three fixed roles: reader, consumer and operator. The other options were roles scoped by a dimension value, and operators only for now | Owner | spec 10 |
| Q107 | Where configuration that changes often lives | **Owner:** a versioned S3 object. Each Allocation records its version. The other options were the Database, and Git | Owner | spec 10, Q136 |
| Q108 | What a correction changes | **Owner:** a stored, versioned override of an input, followed by a rerun. The other options were annotations only, and manual adjustments between owners | Owner | spec 10 |
| Q109 | How writes reach the Database | **Owner:** each control action is a command that the collector applies, so it stays the only writer. The caller gets a command id and polls. The other options were a second writer, and control state kept outside the Database | Owner | ADR 0026 amendment |

## Round 3: the endpoint and the rules for overrides

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q110 | Which front door the API gets | **Owner:** an API Gateway HTTP API in place of the Function URL, with IAM routes and a JWT authorizer. This makes a write endpoint reachable from the internet with a token. The other options were two Function URLs, and CloudFront with a sign-in at the edge | Owner | ADR 0027 amendment |
| Q111 | Which actions consumers can take | **Owner:** direct. Consumers change Cost Dimensions and create overrides, and operators can revert them. The other options were proposals that an operator approves, and Cost Dimensions only | Owner | spec 10 |
| Q112 | What an override can change | **Owner:** both the owner of a workload and the Cost Dimension values on Workload Records. A billed amount never changes, and reconciliation still holds. The other options were each one alone | Owner | spec 10 |
| Q113 | How far back a rerun can go | **Owner:** open periods only: the current month, and the previous one until its bill is final. The other options were any period, and open periods by default with an explicit command for a closed one | Owner | spec 10 |
| Q114 | What the collector does when a command arrives | **Owner:** a full Collection Run each time. The other options were a run with only the commands, and waiting for the next scheduled run | Owner | ADR 0026 amendment |
| Q115 | Where the audit trail is | **Owner:** the command objects in S3, kept under a retention lock. The Database records only their ids. The other options were the Database, and CloudTrail and the logs only | Owner | control-plane ADR |
| Q116 | What happens to the wizards | **Owner:** the CLI takes over everything, including `deploy` and `teardown`, and the wizards retire. The other option was to keep the wizards for setup and teardown | Owner | spec 10 |

## Round 4: sign-in, commands, concurrency

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q117 | How the CLI signs in | **Owner:** both ways. Operators use their AWS profile. Consumers run `data-finops login`, an OIDC device-code flow that caches a short-lived token. The other options were operators only, and OIDC for every person | Owner | spec 10 |
| Q118 | Which OIDC issuer | **Owner:** bring your own (issuer URL, audience, group claim), or Cognito when none is set. The other options were each one alone | Owner | control-plane ADR |
| Q119 | The command catalogue | Reads: `summary`, `allocations`, `rollups`, `reconciliation`, `provenance`, `lineage`, `explain`. Runs: `run`, `runs`, `ingest`. Inbox: `inbox list`, `inbox remove`, `inbox retry`. Cost Dimensions: `dimensions show`, `dimensions set`, `dimensions history`. Overrides: `override owner`, `override dimension`, `overrides list`, `override revert`. Audit: `commands list`, `command show`. Lifecycle: `deploy`, `teardown`, `login`. Q141 and Q132 add commands | Accepted recommendation | spec 10 |
| Q120 | How the CLI deploys | **Owner:** Pulumi's Automation API inside the CLI, as an optional `deploy` extra. The other option was to run the `pulumi` command from Python | Owner | control-plane ADR |
| Q121 | Two overrides on the same target | **Owner:** both are kept and the latest wins, but the conflict is flagged on the Allocation and in `overrides list`. The other options were the last one wins without a flag, and the second one is rejected | Owner | spec 10 |
| Q122 | The retention lock on command objects | **Owner:** a setting, with S3 Object Lock in governance mode for 1 year by default. The other options were fixed governance mode for 1 year, and compliance mode for 1 or 7 years | Owner | spec 10 |
| Q123 | Many commands close together | **Owner:** serialize. The collector gets a reserved concurrency of 1. The other options were a debounce, and accepting conflicts with a Lambda retry | Owner | ADR 0026 amendment |

## Round 5: local use, migration, scope

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q124 | How local commands apply | **Owner:** queued in a local `commands/` folder, and `data-finops run` applies them, as the collector does. The other option was at once, in the same process | Owner | spec 10 |
| Q125 | How the API moves from the Function URL to API Gateway | **Owner:** replace it in one release, with the URL and the reader policy. The other option was both for one release | Owner | spec 10 |
| Q126 | What happens when the account cannot reserve concurrency | **Owner:** fall back to a debounce, with a warning in the deploy record. So the debounce is built too. The other options were that `deploy` refuses, and accepting conflicts | Owner | ADR 0026 amendment |
| Q127 | Whether a Deployment gets a hosted dashboard | **Owner:** yes, with an OIDC PKCE sign-in and control actions. The other options were a read-only hosted dashboard, and none | Owner | spec 10 |
| Q128 | Whether the Public Demo stays as it is | Yes. It stays static and anonymous, with no control plane. ADR 0029 and Q61 are unchanged | Accepted recommendation | spec 09, spec 10 |
| Q129 | How this is built | **Owner:** one milestone, M8 "Control plane", in vertical slices (Q143). The other option was two milestones, with consumers and OIDC in M9 | Owner | plan.md |
| Q130 | The triaged tickets that this overlaps | #213 closes into M8's teardown ticket. #211, #212, #215 and #278 stay as they are | Accepted recommendation | #213 |

## Round 6: browser, users, data shapes, exposure

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q131 | Whether an OIDC group can map to the operator role | **Owner:** yes, with an optional operator group claim. This adds an internet-facing path with operator rights. Q147 limits it. The other option was operator by IAM only | Owner | control-plane ADR |
| Q132 | Who manages users when Cognito is the fallback | **Owner:** `data-finops users add\|remove\|list`, which an operator runs with IAM. The other options were the AWS console only, and self sign-up with approval | Owner | spec 10 |
| Q133 | Where the browser keeps the token | **Owner:** sessionStorage. Q148 adds the protections. The other option was in memory only | Owner | control-plane ADR |
| Q134 | What a command record says about who sent it | **Owner:** only the IAM principal ARN or the OIDC `sub`. No email or name is kept in locked storage. The other option was the principal plus the email claim | Owner | spec 10 |
| Q135 | How far an override reaches | **Owner:** chosen when the override is created: one Billing Period (the default), or from a period onward until it is reverted. The other options were each one alone | Owner | spec 10 |
| Q136 | How the Cost Dimensions object is versioned and seeded | **Owner:** a version number inside the object, plus a content hash. `deploy` seeds the object from Pulumi config once, and after that the CLI owns it. The other options were the S3 version id, and Pulumi config as the source on every `deploy` | Owner | spec 10 |
| Q137 | How the endpoint is protected against abuse and cost | **Owner:** API Gateway throttling as a setting with low defaults (10 requests a second, burst 20), and a limit on waiting commands for each principal (Q153). The other options were throttling plus AWS WAF, and throttling only | Owner | spec 10 |
| Q138 | Where the hosted dashboard is served from | One CloudFront distribution for each Deployment, with `/v1/*` sent to API Gateway, so there is one origin and no CORS. It is separate from the Public Demo's distribution | Accepted recommendation | spec 10 |
| Q139 | When a command is checked | Twice. When it is submitted, the API checks its shape and the caller's role (400 or 403). When it is applied, the collector checks it against the stored state, and a command that fails is marked rejected, with a reason | Accepted recommendation | spec 10 |
| Q140 | Whether commands get `--wait` | Yes. `--wait` polls `command show` until the command is applied or rejected. Without it, the command prints its id | Accepted recommendation | spec 10 |
| Q141 | Whether `dimensions revert <version>` joins the catalogue | Yes. It sets an earlier version as a new version, so the history only grows. An operator or a consumer can run it | Accepted recommendation | spec 10 |
| Q142 | Whether `deploy` and `teardown` do what the wizards do | Yes: the same Access Analyzer checks, the same deploy record, and teardown's `gone` checks from #213. Only the driver changes | Accepted recommendation | spec 10 |
| Q143 | The order of M8's slices | 1. Spec 10, the ADRs, and the CLI target with `summary`. 2. The other reads. 3. API Gateway and IAM routes. 4. The command pipeline. 5. `run`, `runs` and `inbox`. 6. Cost Dimensions in S3. 7. Overrides. 8. `deploy` and `teardown`. 9. OIDC and `login`. 10. The hosted dashboard | Accepted recommendation | plan.md |

## Round 7: storage of commands, and the last rules

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q144 | Where command objects live, given Object Lock | **Owner:** in the data bucket, under `commands/`. The bucket has no default retention. The collector sets the retention on each command object when it writes it. Object Lock is turned on for the data bucket, which cannot be undone. The other option was a separate commands bucket | Owner | control-plane ADR |
| Q145 | What teardown does with command objects that are still locked | **Owner:** it deletes them with a governance bypass, only with `--discard-locked-audit`, and keeps no local copy. The deployer policy gets `s3:BypassGovernanceRetention`. The other options were a local copy first, and refusing until the lock expires | Owner | spec 10 |
| Q146 | Whether the OIDC operator group needs MFA | **Owner:** no. That is the issuer's policy. The other option was an `amr` claim that shows MFA on operator routes | Owner | control-plane ADR |
| Q147 | What stays IAM-only, even for an OIDC operator | `deploy`, `teardown` and `users`. An OIDC operator can run, manage the Inbox and revert | Accepted recommendation | control-plane ADR |
| Q148 | How a token in sessionStorage is protected | A strict Content-Security-Policy from CloudFront (`script-src 'self'`, no inline script, no third-party origins), access tokens of 1 hour or less, and no refresh token in storage | Accepted recommendation | control-plane ADR |
| Q149 | How per-period and onward overrides interact | On one target, the latest wins and the conflict is flagged (Q121). An onward override never reaches into a closed period. Reverting an onward override ends it from the earliest open period | Accepted recommendation | spec 10 |
| Q150 | Who increments the Cost Dimensions version | The collector, when it applies `dimensions set`. The command carries the version that the caller expects, and a stale command is rejected | Accepted recommendation | spec 10 |
| Q151 | Where overrides are stored | As rows in the Database. Only an applied command creates them, and each row records its command id. Each Allocation records the override ids that it used | Accepted recommendation | spec 10 |
| Q152 | How the CLI finds a Deployment | `--deployment <name>` is looked up in `~/.data-finops/deployments.toml`. `deploy` writes it for operators. `login <api-url>` writes it for consumers, from a public `/v1/.well-known` route | Accepted recommendation | spec 10 |
| Q153 | The default limit on waiting commands for each principal | 20, as a setting | Accepted recommendation | spec 10 |
| Q154 | Where these decisions are recorded | This log, before spec 10 and the ADRs | Accepted recommendation | this log |

## Round 8: what an Override of the owner changes (OQ-26)

Spec 10 found that "owner" in Q112 had no meaning in GLOSSARY. Q155–Q160 were asked with options only. Q161 was asked with a recommendation.

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q155 | What `override owner` changes (OQ-26) | **Owner:** both: a Cost Dimension that the configuration marks as the owner, and an Identity Link. The other options were each one alone | Owner | spec 10, ADR 0006 amendment |
| Q156 | The names of the two commands | **Owner:** `override dimension --owner`, a shortcut, and `override link`. "Owner" is not a GLOSSARY term. The other option was `override owner` and a defined term | Owner | spec 10 |
| Q157 | Who can create a link Override | **Owner:** consumers too, as for a dimension Override (Q111). The other option was operators only | Owner | spec 10 |
| Q158 | What a link Override can do to an existing link | **Owner:** replace any link, including one that a Trusted Query Comment makes. The other options were to fill gaps only, and to fill gaps by default with `--replace` for operators | Owner | spec 10, ADR 0006 amendment |
| Q159 | How an Allocation shows a link Override | **Owner:** a new Link Outcome, `override`. The Allocation keeps its Confidence, and its Evidence names the Override id and its principal. The other option was a lower Confidence too | Owner | ADR 0006 amendment, GLOSSARY |
| Q160 | What a link Override can point to | **Owner:** a Model Execution or a Workload Run. The other option was a Model Execution only | Owner | spec 10 |
| Q161 | How the owner dimension is marked | A key, `owner = true`, on at most one dimension in the Cost Dimensions TOML. Without it, `override dimension --owner` exits with 2 and says to mark one | Accepted recommendation | spec 10 |

## Round 9: the tickets

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q162 | File the spec issue and 12 tickets, with blocked-by links and an M8 milestone | Yes: spec issue #287, tickets #288–#299, milestone "M8: control plane" | Accepted recommendation | #287, plan.md |
| Q163 | The test seams of each ticket | Agreed for all 12, as each ticket lists them | Accepted recommendation | #288–#299 |
| Q164 | Which tickets share a PR | #288 with #289 (the CLI), and #294 with #295 (Overrides). Every other ticket has its own PR, so 10 PRs | Accepted recommendation | plan.md |

## How these decisions relate to earlier ones

- **Q72 ("dimension changes should be applied going forward"):** still true for the Cost Dimensions configuration. A new version applies only to Calculation Runs after it. An override (Q108, Q112) is a different thing: an explicit correction to an open period (Q113), which the owner chose in this session.
- **ADR 0027 and AGENTS.md §Security ("no public endpoint"):** Q110 and Q131 change this for a Deployment's API. The ADR 0027 amendment records it, and AGENTS.md changes with it.
- **ADR 0029 and Q61 ("no public compute, no public API"):** unchanged for the Public Demo (Q128).
- **plan.md, Deferred ("enterprise auth"):** Q105 and Q118 bring an OIDC sign-in into M8. Other enterprise auth stays deferred.
- **ADR 0006 (explicit identity Evidence):** Q155–Q160 make an applied link Override a kind of Evidence. The ADR 0006 amendment records it. Time overlap is still never Evidence.
- **ADR 0026 (the collector is the only writer, through a conditional write):** kept. Q109, Q114, Q123 and Q126 add commands, reserved concurrency and the debounce fallback.

## Facts the decisions rest on

- The data bucket already has versioning turned on, and the policy pack requires it (`infra/pulumi/aws/deployment/storage.py`, `policy/policies.py`). S3 lets you turn Object Lock on for an existing versioned bucket, so Q144 needs no new bucket and no data move.
- A browser cannot sign SigV4 requests without AWS credentials. That is why the dashboard works only on a local copy of the Database today (Q127).
- A Lambda Function URL can check IAM, but not an OIDC token (Q110).
- Lambda keeps at least 100 concurrent executions unreserved in each account and Region. New accounts often have a total limit of 10, so they cannot reserve any (Q126).
