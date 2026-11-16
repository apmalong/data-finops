# Control plane (M8)

Decided in the M8 design session (2026-11-16). Each owner decision is logged in `docs/implementation/m8-decisions.md` (Q101–Q160). The design is ADR 0032. It amends ADRs 0026, 0027 and 0031. Spec 08 still applies where this spec does not change it.

## Scope
One CLI controls a local Database or a Deployment: reads, runs, the Inbox, Cost Dimensions, Overrides, the audit trail, and deploy and teardown. Operators sign in with IAM. Consumers sign in with OIDC, in the CLI or in the Deployment Dashboard. The wizards retire.

Out of scope:
- corrections to closed Billing Periods (Q113)
- roles scoped to a dimension value (Q106)
- changes to the Public Demo (Q128)
- changes to ingress: producers still write to the Inbox (ADR 0027)
- enterprise auth other than OIDC, for example SCIM or SAML

## Layout
- `src/data_finops/control/`: Commands (the schema, the queue for each target, and how the collector applies them), Overrides, and the versions of the Cost Dimensions. It imports `ledger` and `collection`, and `cli`, `api` and `lambda_handlers` import it.
- `src/data_finops/cli/`: the target (`--db` or `--deployment`), the command catalog, `login` and `users`.
- `src/data_finops/api/`: the Command routes and `/v1/.well-known/data-finops`.
- `src/data_finops/deploy/`: `deploy` and `teardown` through the Pulumi Automation API, in a new optional `deploy` extra (Q120). It reads `infra/pulumi/aws` from the checkout.
- `infra/pulumi/aws/deployment/`: API Gateway, the JWT authorizer, Cognito, the Deployment Dashboard's distribution, Object Lock, and the operator and consumer policies.
- `infra/pulumi/aws/wizards/`: removed in the last slice of `deploy` and `teardown` (Q116).

## Targets (Q104, Q152)
- `--db PATH`: a local Database. Its control files are in the folder `PATH.control/`, next to it:
  - `commands/`: the queue and the audit trail (Q124)
  - `config/cost-dimensions/`: the versions of the Cost Dimensions
- `--deployment NAME`: a Deployment. The CLI reads `NAME` from `~/.data-finops/deployments.toml`:
  - `api_url` and `region`, for each Deployment
  - `profile` (operators), or `issuer`, `client_id` and `audience` (consumers)
- `deploy` writes the entry for operators. `login API_URL` writes it for consumers. It reads the issuer, the client id and the audience from the public route `GET /v1/.well-known/data-finops`.
- The file holds no token and no secret. `login` keeps the token in the credential store of the OS, or in a file that only the user can read.
- A command that is given neither target, or both, exits with 2.

## Roles and sign-in (Q105, Q106, Q117, Q118, Q131, Q147)
| Role | Can | IAM | OIDC |
|---|---|---|---|
| reader | each read | the reader policy | the reader group |
| consumer | reads, `dimensions set` and `revert`, and `override` and `override revert` of its own Overrides | the consumer policy | the consumer group |
| operator | each consumer action, `run`, `inbox remove` and `retry`, and `override revert` of any Override | the operator policy | the operator group (optional) |

- `deploy`, `teardown` and `users` need IAM, with the deployer's credentials. No API route does them.
- **The OIDC issuer:** the settings `oidcIssuer`, `oidcAudience` and `oidcGroupsClaim`, and a map from group to role (`oidcRoleGroups`). Without `oidcIssuer`, the stack creates a Cognito user pool with three groups.
- **The CLI:** `login` runs the OIDC device-code flow (RFC 8628). An operator uses an AWS profile.
- **MFA:** that is the policy of the issuer. data-finops does not check it (Q146).
- **Users:** `users add EMAIL --role ROLE`, `users remove EMAIL` and `users list` manage the Cognito user pool. With your own issuer, they exit with 2 and say that the issuer manages users (Q132).

## Command catalog (Q119, Q141)
Each command works against `--db` and `--deployment`, except where the table says otherwise. A command that writes is a Command (below).

| Group | Commands | Writes a Command |
|---|---|---|
| Reads | `summary`, `allocations`, `rollups [--dimension NAME]`, `reconciliation`, `provenance`, `lineage`, `explain REF` | no |
| Runs | `run`, `runs`, `ingest …` (local only, as today) | `run` |
| Inbox | `inbox list`, `inbox remove KEY`, `inbox retry KEY` (Deployment only) | `remove`, `retry` |
| Cost Dimensions | `dimensions show [--version N]`, `dimensions set FILE`, `dimensions history`, `dimensions revert N` | `set`, `revert` |
| Overrides | `override dimension …`, `override link …`, `overrides list`, `override revert ID` | `override …`, `revert` |
| Audit | `commands list`, `command show ID` | no |
| Lifecycle | `deploy`, `teardown`, `login`, `users …` | no |

- A read gives the same JSON as its HTTP route (spec 06), with `--format json`, or a table by default. Reads take `--calculation-run-id` and `--source` where the route does.
- `data-finops serve`, `demo`, `calculate` and `collect` stay as they are.
- A Command prints its id and returns. With `--wait`, it polls `command show` until the Command is applied or rejected (Q140). The exit code is 0 when it is applied, and 1 when it is rejected.

## Commands (Q109, Q114, Q115, Q123, Q124, Q134, Q139)
A Command is one JSON object, `commands/<command_id>.json`. It never changes after it is written.

```json
{
  "schema": "data-finops.command/1",
  "command_id": "c-20261116T101500Z-3f9a2b7c",
  "kind": "dimensions.set",
  "args": {"expected_version": 3, "toml": "..."},
  "principal": {"kind": "oidc", "id": "<sub>"},
  "role": "consumer",
  "submitted_at": "2026-11-16T10:15:00Z"
}
```

- **`command_id`:** the UTC time and 8 random hex characters, so a list in key order is in time order.
- **`kind`:** `run`, `inbox.remove`, `inbox.retry`, `dimensions.set`, `dimensions.revert`, `override.dimension`, `override.link` or `override.revert`.
- **`principal`:** the IAM principal ARN, or the OIDC `sub`. Never a name or an email (Q134).
- **The limits:** 64 KiB for each object. Each principal can have 20 queued Commands (`maxQueuedCommands`, Q153). One more is refused with 429.
- **On submit,** the API checks the schema, the kind, the arguments and the caller's role. It answers 400 or 403, or it writes the object and answers 202 with the id (Q139). The schema is generated into `schemas/` with the other contracts, and the OpenAPI document shows the routes.
- **On apply,** a Collection Run applies the queued Commands first, in `command_id` order, then collects its sources. It checks each Command against the stored state. A Command that fails is rejected, with a reason that names no content. The Database records each `command_id`, its result, its reason and the Collection Run that applied it.
- **A Command is queued** when its object exists and the Database has no result for it.
- **The exit code of the run** is 1 when a Command was rejected, as for a rejected input (spec 05).
- **The routes:** `POST /v1/commands`, `GET /v1/commands` (paged) and `GET /v1/commands/{id}`. `GET` shows `queued`, `applied` or `rejected`.

### How a Command starts a run (Q123, Q126)
- The API starts the collector with an asynchronous invoke after each Command. The collector has a reserved concurrency of 1, so Lambda keeps the extra invokes and tries them again later. A run that has no Command to apply and no new input stores nothing.
- **Without the quota:** `deploy` sets no reserved concurrency. The API then creates a one-time EventBridge Scheduler schedule with a fixed name, `commandDebounceSeconds` (default 300) later. When the schedule already exists, the API does nothing. The deploy record says that the debounce is in use.
- The daily schedule stays.

### Local (Q124)
`--db` Commands are written to `PATH.control/commands/`, with the same schema. `principal` is `{"kind": "local", "id": "<user name>"}`. `data-finops run --db PATH` applies them and calculates. There is no lock and no retention on local files.

## Cost Dimensions versions (Q107, Q136, Q141, Q150)
- Each version is its own object: `config/cost-dimensions/v<N>.toml`. The TOML has the format of spec 09, with a new top-level `version = N`.
- The content hash is the SHA-256 of the object's bytes. Each Allocation records `dimensions_version` and `dimensions_sha256`.
- The current version is the one with the highest `N`. A version is never changed or deleted.
- `deploy` writes `v1` from the `costDimensions` setting, only when no version exists. After that, the setting is not read again, and `deploy` says so when it is changed.
- Only the collector writes `config/cost-dimensions/`. Its role gets `s3:PutObject` there, with `If-None-Match: *`, so a version is never overwritten.
- `dimensions set FILE` carries the version that the caller read. The collector writes `v<N+1>`, or it rejects the Command as stale when another version came first.
- `dimensions revert N` writes the content of `v<N>` as a new version.
- A new version applies only to later snapshots and does not, by itself, start a new snapshot (ADR 0031, Q72).
- Locally, `--dimensions FILE` still works for one `ingest`, `calculate` or `collect`. That run records the hash and no version.
- The setting `costDimensions` and the key `config/cost-dimensions.toml` of spec 09 are replaced. The first `deploy` of M8 copies the `config/cost-dimensions.toml` of spec 09, when there is one, to `v1`.
- `config/` has no lifecycle expiry.

## Overrides (Q108, Q112, Q113, Q121, Q135, Q149, Q151)
- **What an Override changes:**
  - `override dimension NAME VALUE`: the value of one Cost Dimension for one Query Execution, or for each Query Execution of a Model Execution or a Workload Run.
  - `override dimension --owner VALUE`: the same, for the dimension that the configuration marks with `owner = true` (Q156, Q161). At most one dimension has the mark. Without it, the command exits with 2 and says to mark one.
  - `override link QUERY_EXECUTION --to REF`: the Identity Link of one Query Execution, to a Model Execution or a Workload Run (Q155, Q160). It replaces any other link of that Query Execution, also a link from a Trusted Query Comment (Q158). The link has the Link Outcome `override`. Its Evidence names the Override id and the principal (Q159, ADR 0006 amendment). The Allocation keeps the Confidence of its method.
  - A consumer or an operator can create either kind (Q111, Q157).
  - An Override never changes a billed amount. Reconciliation still holds.
- **Reach:** `--period YYYY-MM` (the default) covers one open Billing Period. `--from YYYY-MM` covers that period and each later one, until it is reverted.
- **Open periods:** the current Billing Period, and the one before it until its bill is final. A Command that names a closed period is rejected.
- **Storage:** a row in the Database, with its `command_id`, the principal, and when it was applied. Only an applied Command creates one.
- **Calculation:** the ids of the Overrides that apply are part of the input digest. A new Override or a revert makes a new Allocation Snapshot for the open periods that it reaches. Each Allocation records the Override ids that it used. Its `dimensions` value shows the source `override`.
- **Conflicts:** two Overrides on one target are both kept. The latest one wins, and both the Allocation and `overrides list` flag the conflict.
- **Revert:** `override revert ID` ends the Override from the earliest open period. A consumer can revert only its own Overrides. An operator can revert any of them.

## The API (Q110, Q125, Q137)
- An API Gateway HTTP API replaces the Function URL in one release. The API function stays, and serves through the same FastAPI app.
- IAM routes are under `/v1/`. JWT routes are under `/v1/o/`, with the same paths. The JWT authorizer checks the issuer, the audience and the expiry. The app maps the group claim to a role.
- **Policies (outputs):** the reader policy grants `execute-api:Invoke` on the read routes. The consumer and operator policies grant the routes of their role. The reader policy no longer grants `lambda:InvokeFunctionUrl`.
- **The API role** is no longer read-only. It also gets:
  - `s3:PutObject` and `s3:PutObjectRetention` on `commands/*`, and `s3:GetObject` and `s3:ListBucket` on `commands/`
  - `lambda:InvokeFunction` on the collector only
  - when the debounce is in use, `scheduler:CreateSchedule` on its one schedule name, and `iam:PassRole` for the scheduler role only
  - It still cannot write the Database object, `config/` or the Inbox.
- **The collector role** also gets `s3:GetObject` and `s3:ListBucket` on `commands/`, `s3:DeleteObject` on `inbox/*` for `inbox remove`, and the `config/` write above. `inbox retry` removes the key from the Collection Watermark, so the next run collects it again.
- **Rate limits:** `apiRateLimit` (default 10 requests a second) and `apiBurstLimit` (default 20).
- **The kill switch** is now: set the rate limits of the API stage to 0.

## The Deployment Dashboard (Q127, Q133, Q138, Q148)
- A CloudFront distribution for each Deployment, separate from the Public Demo's. It serves the dashboard from a private bucket prefix through origin access control, and sends `/v1/*` to API Gateway.
- **Sign-in:** OIDC authorization code with PKCE. The access token is kept in sessionStorage. No refresh token is stored. The token lifetime of the app client is 1 hour or less.
- **Response headers:** `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`, plus HSTS, `X-Content-Type-Options` and `Referrer-Policy`.
- **Control actions:** a consumer edits the Cost Dimensions and creates and reverts its own Overrides. An operator also runs, manages the Inbox and reverts any Override.
- It is the same dashboard code as the local one and the Public Demo's, in a third mode.

## The audit trail (Q115, Q122, Q134, Q144, Q145)
- Object Lock is turned on for the data bucket, with no default retention. This cannot be undone.
- The API writes each Command with a governance-mode retention of `commandRetentionDays` (default 365). A retention of 0 writes no lock.
- The data bucket's lifecycle rules do not expire `commands/`.
- `commands list` and `command show` read the objects and their results.
- **Teardown:** without `--discard-locked-audit`, `teardown` stops before it deletes the bucket while Commands are locked, and names how many. With the flag, it deletes them with `s3:BypassGovernanceRetention`, and keeps no copy. The deployer policy gets that action.

## Deploy and teardown (Q116, Q120, Q142)
- `data-finops deploy --stack NAME` and `teardown --stack NAME` use the Pulumi Automation API, in the `deploy` extra. They keep each step of the wizards:
  - the checks before the deploy
  - `preview` with the policy pack, then `up`
  - IAM Access Analyzer on the real policies
  - the retain-on-delete check
  - the masked deploy record
  - teardown's `gone` check for each AWS resource type, with and without the Public Demo (#213)
- The deploy record also says whether the reserved concurrency or the debounce is in use.
- Only the owner runs `deploy` and `teardown` against a real account. Agents never do.

## Settings (spec 08 additions)
| Setting | Default | Meaning |
|---|---|---|
| `oidcIssuer`, `oidcAudience`, `oidcGroupsClaim` | unset, unset, `groups` | Your own OIDC issuer. Without `oidcIssuer`, a Cognito user pool is created |
| `oidcRoleGroups` | `{reader: "data-finops-reader", consumer: "data-finops-consumer"}` | The group for each role. `operator` is optional |
| `apiRateLimit`, `apiBurstLimit` | 10, 20 | API Gateway rate limits |
| `maxQueuedCommands` | 20 | Queued Commands for each principal |
| `commandRetentionDays` | 365 | Object Lock governance retention of Commands |
| `commandDebounceSeconds` | 300 | Only when the collector cannot reserve concurrency |
| `dashboard` | `true` | The Deployment Dashboard |

`costDimensions` seeds `v1` only. `apiReservedConcurrency` stays.

## Documentation (spec 07)
- **How-to pages, in place of the wizard pages:**
  - use the CLI against a Deployment
  - sign in as a consumer
  - change Cost Dimensions
  - correct an Allocation with an Override
  - deploy and tear down with the CLI
- **Reference:** the CLI page, the HTTP API page with the Command routes, the generated settings page, the Command schema, and the exit codes.
- **Explanation:**
  - a new page: the control plane and its trust boundaries
  - the security model page
  - the deployment architecture page, and `deployment.d2`
- **Specs, as each part lands:** spec 02 (the Link Outcome `override`, and the Override ids on an Allocation), spec 03 (link Overrides), spec 05 (the CLI and the endpoint), spec 06 (the routes and the third dashboard mode), and spec 08 (the API, the bucket and IAM).

## Tests
Each seam is test-first, with hand-made mutation checks recorded in the PR. No test touches a real account. The seams of each ticket are agreed with the owner before it is built (AGENTS.md).

## Exit
- Each command in the catalog works against `--db` and, under stubs, against `--deployment`.
- A Command sent through the API is applied by a Collection Run, and `command show` reports it.
- A consumer with an OIDC token can set the Cost Dimensions and create an Override. An operator can revert them.
- The policy pack checks the new resources, and the infra job passes.
- The owner runs `deploy` on a real account and records it in progress.md, with a Command applied end to end.
