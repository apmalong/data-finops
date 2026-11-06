# ADR 0027: A Deployment receives OpenLineage events and dbt artifacts by scheduled pull from S3 Inboxes

Status: Accepted (design, 2026-11-06). Decides OQ-5's production ingress and ADR 0024 point 6. Research: `docs/research/m6-openlineage-ingress.md`.

## Context
ADR 0024 left production ingress to M6: "a private API behind cloud IAM, or scheduled pull". In openlineage-python 1.54.0 (and 1.55.0), the HTTP transport's auth types are `api_key`, `jwt`, `oauth2` or a custom token provider, and a token provider can only set the `Authorization` header. SigV4 signs the body and several headers, so no stock client (dbt-ol, the Airflow provider) can call an `AWS_IAM` Function URL without a custom transport. The file transport writes one single-line JSON file per event, which M2's JSON Lines ingester already reads. It can write to `s3://` through fsspec, which also needs s3fs (unverified). No OpenLineage receiver offers a pull API.

## Decision
1. Producers push nothing over HTTP to a Deployment. They write OpenLineage event files under `inbox/openlineage/` and dbt `manifest.json` and `run_results.json` under `inbox/dbt/<invocation>/` in the Deployment's bucket (ADR 0026).
2. The stack outputs a producer IAM policy allowing only `s3:PutObject` on `inbox/*`, which the operator attaches to the Airflow or dbt identity. Authentication is the IAM principal; there is no shared secret.
3. Each Collection Run pulls Inbox objects not yet in its Collection Watermark, with the existing caps (1 MiB per event, 512 MiB per dbt artifact), and records each processed key.
4. The HTTP receiver (ADR 0024) stays a local and self-hosted feature; the deployed API does not mount it.
5. The how-to gives two producer set-ups: the file transport writing locally and `aws s3 sync` after the run (proven), or the file transport writing to `s3://` directly (needs fsspec and s3fs; unverified).

## Consequences
Lineage reaches the Deployment at the next Collection Run, not in real time; costs only change at a Calculation Run anyway (spec 06). A received file that fails screening is reported in the Collection Run's summary by key and reason, never by content. Inbox objects expire after a configured number of days, so a Deployment that stops collecting for longer loses them; the summary warns.

## Alternatives rejected
- **HTTP to an `AWS_IAM` Function URL:** needs a custom SigV4 transport shipped to every producer.
- **HTTP with the static bearer token on a public URL:** a long-lived shared secret on the internet; ADR 0024 keeps it for local use.
- **OAuth2 client credentials against an API Gateway JWT authorizer:** works with stock clients but adds an identity provider (Cognito) and API Gateway for a feature a pull delivers without them.

## Revisit triggers
Producers need lineage in near real time; several teams need separate credentials beyond IAM principals; a stock client gains SigV4 or an S3 transport.
