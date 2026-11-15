# ADR 0024: The OpenLineage receiver is loopback by default and token-gated off it

Status: Accepted (design, 2026-11-03). Decides OQ-6 for local use. Amended 2026-11-06: production ingress (point 6) is decided by ADR 0027.

## Context
M5 adds an HTTP receiver for OpenLineage RunEvents beside the read API (spec 04). ADR 0012 and spec 04 require private ingestion by default and authenticated production ingress. plan.md defers enterprise authentication. Each OpenLineage client must be configured with the address and the credentials of the receiver. Thus the ingress pattern is costly to change after pipelines send to it. A local demo must still start with one command and no secrets.

## Decision
1. The server binds to `127.0.0.1` unless told otherwise. On loopback, a token is optional.
2. The receiver accepts one static bearer token (`Authorization: Bearer <token>`, the header OpenLineage's HTTP transport sends for its `api_key` auth), read from an environment variable or a file. The token is never logged, echoed or stored.
3. When a token is configured, every receiver request without it, or with a different one, is refused with 401, compared in constant time.
4. The server refuses to start bound to any non-loopback address unless a token is configured.
5. Request bodies above the 1 MiB event cap are refused with 413 before parsing.
6. Production ingress (a private API behind cloud IAM, or scheduled pull) is decided with the deployment topology in M6 (OQ-5), not here.

## Consequences
The local demo does not require a secret. To expose the receiver on a network, a deliberate token is necessary. Thus a bind with a wrong configuration fails closed. One shared token cannot tell senders apart, so it carries no identity and is never Evidence (ADR 0006). The read API and dashboard follow the same bind rule and token check.

## Alternatives rejected
- No authentication in M5: an accidental `0.0.0.0` bind would accept forged runs from anyone on the network (spec 05 threat model).
- Mutual TLS or OAuth now: heavier than a local demo requires. Enterprise authentication is deferred (plan.md).
- A token required also on loopback: it adds a secret to the one-command demo, but it does not stop a local attacker who can already read the database file.

## Revisit triggers
- More than one team sends events to one receiver, and the teams require separate credentials.
- The M6 deployment decides the production ingress pattern.

## Amendment (2026-11-06, M6)
Point 6 is decided by ADR 0027: a Deployment receives OpenLineage events by scheduled pull from an S3 Inbox, authenticated by IAM. The receiver stays a local and self-hosted feature and is not mounted by the deployed API (ADR 0026).
