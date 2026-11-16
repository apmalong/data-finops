# ADR 0032: The control plane sends Commands through the collector, behind an API Gateway with IAM and OIDC

Status: Accepted (design, 2026-11-16). Decides M8's control plane (Q101–Q160, `docs/implementation/m8-decisions.md`). Amends ADR 0006 (link Overrides), ADR 0026 (points 2, 3 and 4), ADR 0027 (an endpoint on the internet) and ADR 0031 (points 1 and 5). Keeps ADR 0029: the Public Demo stays static (Q128). Spec 10 has the details.

## Context
Today the CLI only writes to a local Database, and the API of a Deployment only reads. An operator controls a Deployment with Pulumi config, the wizards, a daily schedule and `aws lambda invoke`. A correction is a manual edit in S3. The owner wants a platform team to operate data-finops, and consumers to make some changes without AWS access (Q101, Q102).

ADR 0026 makes the collector the only writer of the Database object, through a conditional write. ADR 0027 has no public endpoint. A Lambda Function URL can check IAM, but not an OIDC token. A browser cannot sign SigV4.

## Decision
1. **One CLI with a target.** Each command works against `--db PATH` or `--deployment NAME`, and does the same job in both places (Q104). `--deployment` finds its endpoint in `~/.data-finops/deployments.toml`, which `deploy` or `login` writes (Q152).
2. **Commands, not writers.** Each control action is a Command. A Command is an S3 object under `commands/` in the bucket of the Deployment. The API checks its shape and the caller's role, writes it, and starts the collector. The collector applies it in a full Collection Run, so it stays the only writer (Q109, Q114, Q139). The caller gets a command id and polls (`--wait`, Q140). A local target queues Commands in a folder, and `run` applies them (Q124).
3. **Serialized.** The collector has a reserved concurrency of 1 (Q123). An account that cannot reserve it gets a debounce, and the deploy record warns (Q126).
4. **API Gateway.** An HTTP API replaces the Function URL in one release (Q110, Q125). IAM authorizes operator and reader routes. A JWT authorizer authorizes OIDC routes. The issuer is the operator's own (issuer URL, audience, group claim), or a Cognito user pool when none is set (Q118). Throttling and a limit of queued Commands for each principal are settings (Q137, Q153).
5. **Three roles.** Reader, consumer and operator (Q106). Consumers change Cost Dimensions and create Overrides directly, and operators can revert them (Q111). An OIDC group can map to each role, operator included (Q131). `deploy`, `teardown` and `users` stay IAM-only (Q147).
6. **Overrides.** An Override replaces a Cost Dimension value or an Identity Link for one open Billing Period, or from a period onward (Q112, Q113, Q135, Q155). A link Override is Evidence with the Link Outcome `override`, and it can replace any link (Q158, Q159, ADR 0006 amendment). It never changes a billed amount. It is a row in the Database, made only by an applied Command. Its id is part of the input digest, so a Calculation Run with a new Override makes a new Allocation Snapshot (Q151). On one target, the latest wins, and the conflict is flagged (Q121, Q149).
7. **Versioned Cost Dimensions.** Each version of the configuration is its own object, with a version number and a content hash in it. `deploy` seeds version 1 from the `costDimensions` setting one time. After that, the CLI owns it (Q107, Q136, Q150). Each Allocation records the version it used.
8. **An audit trail under Object Lock.** Command objects keep a governance-mode retention, which is a setting with a default of 1 year (Q115, Q122). The API sets it on each object when it writes it (Q144 said the collector, but the API is the writer of a Command). Object Lock is turned on for the existing data bucket, with no default retention (Q144). A Command records the IAM principal ARN or the OIDC `sub`, never a name or email (Q134). `teardown --discard-locked-audit` deletes them with a governance bypass (Q145).
9. **A Deployment Dashboard.** CloudFront serves the dashboard for each Deployment, with `/v1/*` sent to API Gateway, so it has one origin. It signs in with OIDC PKCE and has the control actions (Q127, Q138). It keeps the token in sessionStorage, behind a strict Content-Security-Policy, with access tokens of 1 hour or less and no stored refresh token (Q133, Q148).
10. **Deploy and teardown in the CLI.** `deploy` and `teardown` drive Pulumi through its Automation API, as an optional extra. They keep the Access Analyzer checks, the deploy record and the `gone` checks, and the wizards retire (Q116, Q120, Q142).

## Consequences
- **An endpoint that writes is on the internet.** It needs IAM or a valid token, and it only queues Commands. Rate limits, the Command limit, the reserved concurrency and the Object Lock limit what a stolen token can do. A stolen operator token can still run, revert and empty the Inbox (Q131, Q146). MFA is the issuer's policy.
- **A consumer can move cost between Query Executions** with a link Override, also away from a link that a Trusted Query Comment makes (Q157, Q158). The Evidence, the audit trail and the conflict flag show who did it, and an operator can revert it.
- **Each Command costs a full Collection Run** (Q114). With serialization, many Commands make a queue, not conflicts.
- **Object Lock cannot be turned off** on the data bucket after it is turned on.
- **The browser keeps a token where page script can read it** (Q133). The Content-Security-Policy is the main control.
- **Reruns cover open periods only** (Q113). A closed period keeps its snapshot, and an Override never reaches into it.
- **The wizards and the Function URL go away.** The how-to pages and the reader policy change in the same release (Q125).
- **A new wire format:** the Command object. ADR 0014 asks for an ADR to add one. This ADR is that record, and spec 10 gives its schema.

## Alternatives rejected
- **A read-only API, with control through AWS's own APIs:** consumers without AWS access could not act (Q103).
- **A second writer of the Database:** two conditional writers fight over one object (Q109).
- **Two Function URLs, one without auth that checks the JWT itself:** the function would be the only check (Q110).
- **IAM Identity Center for consumers:** every consumer needs an AWS identity (Q105).
- **A separate commands bucket with a default retention:** one more bucket to secure and tear down. The data bucket already has versioning, which Object Lock requires (Q144).
- **Compliance-mode retention:** no one, not even the root user, can shorten it (Q122).
- **AWS WAF:** about $5 or more a month (Q137).

## Revisit triggers
- More Commands than a serialized collector can apply each day.
- A Command must apply faster than a full Collection Run.
- Consumers need roles scoped to a dimension value (Q106).
- Corrections to closed periods (Q113).
- An issuer that cannot give a group claim.
