# ADR 0024: The OpenLineage receiver is loopback by default and token-gated off it

Status: Accepted (design, 2026-11-03). Decides OQ-6 for local use; production ingress stays with OQ-5 (M6).

## Context
M5 adds an HTTP receiver for OpenLineage RunEvents beside the read API (spec 04). ADR 0012 and spec 04 require private ingestion by default and authenticated production ingress; plan.md defers enterprise auth. Every OpenLineage client must be configured with the receiver's address and credentials, so the ingress pattern is costly to change once pipelines send to it. A local demo must still start with one command and no secrets.

## Decision
1. The server binds to `127.0.0.1` unless told otherwise. On loopback, a token is optional.
2. The receiver accepts one static bearer token (`Authorization: Bearer <token>`, the header OpenLineage's HTTP transport sends for its `api_key` auth), read from an environment variable or a file. The token is never logged, echoed or stored.
3. When a token is configured, every receiver request without it, or with a different one, is refused with 401, compared in constant time.
4. The server refuses to start bound to any non-loopback address unless a token is configured.
5. Request bodies above the 1 MiB event cap are refused with 413 before parsing.
6. Production ingress (a private API behind cloud IAM, or scheduled pull) is decided with the deployment topology in M6 (OQ-5), not here.

## Consequences
The local demo needs no secret. Exposing the receiver on a network requires a deliberate token, so a misconfigured bind fails closed. One shared token cannot tell senders apart, so it carries no identity and is never Evidence (ADR 0006). The read API and dashboard follow the same bind rule and token check.

## Alternatives rejected
- No authentication in M5: an accidental `0.0.0.0` bind would accept forged runs from anyone on the network (spec 05 threat model).
- Mutual TLS or OAuth now: heavier than a local demo needs; enterprise auth is deferred (plan.md).
- A token required even on loopback: adds a secret to the one-command demo without stopping a local attacker who can already read the database file.

## Revisit triggers
Several teams send events to one receiver and need separate credentials; the M6 deployment fixes the production ingress pattern.

## Amendment (2026-11-06, M6)
Point 6 is decided by ADR 0027: a Deployment receives OpenLineage events by scheduled pull from an S3 Inbox, authenticated by IAM. The receiver stays a local and self-hosted feature and is not mounted by the deployed API (ADR 0026).
