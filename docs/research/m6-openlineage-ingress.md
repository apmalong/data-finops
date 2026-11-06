# M6: OpenLineage ingress options for the AWS deployment

**Status:** research, 2026-11-06. Input for OQ-5 and ADR 0024 point 6. Nothing here is decided yet.

**Question:** what can stock OpenLineage producers (openlineage-python and dbt-ol 1.54.0, Airflow 3.3.2 with apache-airflow-providers-openlineage 2.20.1) send to a production data-finops receiver without custom code? The candidates are a private API behind cloud IAM, or a scheduled pull.

**Method:** source read at release tags; documentation pages fetched on 2026-11-06. No AWS account was called and no credentials were used. Anything marked **(inference)** was reasoned from the source, not run. Anything marked **(unverified)** was not checked.

## Sources and versions

| What | Version, commit | Checked |
|---|---|---|
| openlineage-python, pinned | 1.54.0, PyPI upload 2026-10-12. Tag `1.54.0` → commit `5688b2fd7f39bc9845ada52c5758327e4706edf5` ([tree](https://github.com/OpenLineage/OpenLineage/tree/1.54.0/client/python/src/openlineage/client/transport)) | 2026-11-06 |
| openlineage-python, latest | **1.55.0**, PyPI upload 2026-10-29. Tag `1.55.0` → commit `5ec09f180d0857327a58b85531b7447d069abf07` | 2026-11-06 |
| openlineage-dbt | 1.54.0, same tag (`integration/dbt`) | 2026-11-06 |
| apache-airflow-providers-openlineage, pinned | 2.20.1, PyPI upload 2026-08-23. Tag `providers-openlineage/2.20.1` → commit `e361f70cf5e9c7a9f1b1bb33cc6e4944cfc86a24` ([provider](https://github.com/apache/airflow/tree/e361f70cf5e9c7a9f1b1bb33cc6e4944cfc86a24/providers/openlineage)) | 2026-11-06 |
| apache-airflow-providers-openlineage, latest | 2.21.0, PyPI upload 2026-11-01, commit `fb6534f1db4c57cf34be78c57d07a481d21ff7af`. Only its changelog was read | 2026-11-06 |
| OpenLineage docs | https://openlineage.io/docs/client/python/configuration (the page says 1.55.0) | 2026-11-06 |
| AWS docs | Lambda [urls-auth](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html) and [urls-invocation](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html); API Gateway [HTTP API JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html) and [private REST APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html); IAM [Create a signed AWS API request](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html) | 2026-11-06 |

From 1.54.0 to 1.55.0, `client/python` changed in only one way: how values are parsed (commit "client: keep env/YAML values as text for string-typed settings", #5111, 2026-10-29). Strings that look like numbers or booleans in `OPENLINEAGE__*` variables and YAML (API keys, `log_file_path`, header values) now stay text. No transport was added or removed. Everything below therefore holds for both versions. Source paths are relative to `client/python/src/openlineage/client/`.

## 1. Transports shipped

`transport/__init__.py` registers the following transports with the default factory (identical in 1.54.0 and 1.55.0):

| `type` | Module | Config keys (from the `Config` class) | Extra dependency |
|---|---|---|---|
| `http` | `transport/http.py` | `url` (required), `endpoint` (default `api/v1/lineage`), `timeout` (5.0), `verify`, `auth`, `compression` (`gzip`), `custom_headers`, `retry` (urllib3 Retry kwargs; default total 5, `status_forcelist` 500/502/503/504), `sslContext` (`caCertPath`, `clientCertPath`, `clientKeyPath`: CA bundle and mTLS). `session` and `adapter` exist but are marked "not set by TransportFactory" | none (requests) |
| `async_http` | `transport/async_http.py` | the `http` keys plus `max_queue_size`, `max_concurrent_requests`. Uses the same `create_token_provider` | none (httpx2) |
| `file` | `transport/file.py` | `log_file_path` (required), `append` (false), `storage_options`, `filesystem`, `fs_kwargs`, `debug_mode` | `[fsspec]` for remote paths |
| `console` | `transport/console.py` | none | none |
| `noop` | `transport/noop.py` | none (also chosen when `OPENLINEAGE_DISABLED=true`) | none |
| `kafka` | `transport/kafka.py` | `config` (librdkafka dict, required), `topic`, `messageKey`, `flush` (true) | `[kafka]` confluent-kafka |
| `msk-iam` | `transport/msk_iam.py` | the `kafka` keys plus `region` (else `AWS_REGION` or the boto3 session region), `aws_profile`, `role_arn`, `aws_debug_creds`. Sets SASL_SSL with OAUTHBEARER through `aws-msk-iam-sasl-signer-python` | `[msk-iam]` |
| `amazon_datazone_api` | `transport/amazon_datazone.py` | `domainId`, `region`, `endpointOverride`. Calls `boto3.client("datazone").post_lineage_event`; RunEvents only | `[datazone]` boto3 |
| `datadog` | `transport/datadog.py` | `apiKey` (or `DD_API_KEY`), `site`, `timeout`, `retry`, queue sizes, `async_transport_rules` | none |
| `gcplineage` | `transport/gcplineage.py` | `project_id`, `location`, `credentials_path`, `async_transport_rules` | `[gcplineage]` |
| `composite` | `transport/composite.py` | `transports` (list or name→config map), `continue_on_failure` (true), `continue_on_success` (true), `sort_transports` | none |
| `transform` | `transport/transform/transform.py` | `transport` (inner transport), `transformer_class`, `transformer_properties` | none |

**Not shipped in Python:** no `s3`, `gcs` or `kinesis` transport. S3 and GCS are reached only through the `file` transport with fsspec (section 3). The Java client has a separate S3 transport (`client/java/transports-s3`, added in 1.23.0, 2024-10-04, CHANGELOG #3129) and a Kinesis transport. Neither is usable from dbt-ol or Airflow, which run the Python client. (A CHANGELOG line for 1.35.0, #3857, says "Python: Implement Transport.close() for Datazone and Kinesis", but no Python Kinesis module exists at 1.54.0.)

**Custom transports:** `DefaultTransportFactory._create_transport` (`transport/factory.py`) looks up `type` among the registered names. If it is not found, it imports `type` as a dotted class path (`import_from_string`) and requires a `Transport` subclass. So `type: mypkg.module.MyTransport` works from any config source, provided the package is installed where the producer runs.

**How configuration is found** (`client.py`, `OpenLineageClient.config` and `_resolve_transport`):
1. `OPENLINEAGE_DISABLED=true` → noop.
2. A transport object passed to the client.
3. Merged config, in order of precedence: user-defined (constructor) > the YAML file (`OPENLINEAGE_CONFIG`, then `./openlineage.yml`, then `~/.openlineage/openlineage.yml`) > `OPENLINEAGE__*` env vars. Env vars split on `__`; **keys are lowercased**; each value is parsed as JSON when possible, otherwise kept as a string. So `OPENLINEAGE__TRANSPORT__TYPE=http`, `OPENLINEAGE__TRANSPORT__URL=...`, `OPENLINEAGE__TRANSPORT__AUTH='{"type":"api_key","apiKey":"..."}'`, `OPENLINEAGE__TRANSPORT__CUSTOM_HEADERS='{"x-api-key":"..."}'`. The JSON form is needed for any key with a hyphen or capitals, because env var names cannot carry them.
4. Legacy: `OPENLINEAGE_URL`, `OPENLINEAGE_ENDPOINT` and `OPENLINEAGE_API_KEY` build an `http` transport with `api_key` auth (`_http_transport_from_env_variables`).
5. Otherwise, console.

**dbt-ol:** `integration/dbt/src/openlineage/dbt/__init__.py` calls a bare `OpenLineageClient()`, so it sees every source above: YAML, `OPENLINEAGE__*` and `OPENLINEAGE_URL`. It has no transport settings of its own. When an emit fails, dbt-ol logs a warning and **drops the event**: `except Exception` → `logger.warning("OpenLineage client failed to emit event ...")`. The dbt run is not failed.

**Airflow provider:** section 5.

## 2. HTTP transport authentication

`create_token_provider` (`transport/http.py`) maps `auth.type` as follows:

| `auth.type` | Class | Sends |
|---|---|---|
| absent | `TokenProvider` | no Authorization header |
| `api_key` | `ApiKeyTokenProvider` (`apiKey`, `apikey` or `api_key`) | `Authorization: Bearer <apiKey>` |
| `jwt` | `JwtTokenProvider`: POSTs `apiKey` to `tokenEndpoint` (form fields `grant_type`, `response_type`, `apikey`), caches the token, refreshes it `tokenRefreshBuffer` (120 s) before expiry | `Authorization: Bearer <token>` |
| `oauth2` | `OAuth2ClientCredentialsTokenProvider`: RFC 6749 §4.4 client credentials (`clientId`, `clientSecret`, `tokenEndpoint`, `scope`, `clientAuthMethod` = `client_secret_basic` or `client_secret_post`), cached and refreshed. CHANGELOG: added in **1.54.0** | `Authorization: Bearer <access_token>` |
| any other string | imported as a dotted class path; must subclass `TokenProvider`; constructed with the whole `auth` dict | whatever `get_bearer()` returns |

**A custom TokenProvider can set only the Authorization header.** The interface is `get_bearer(self) -> str | None`, which takes no arguments. `HttpTransport._auth_headers` turns its return value into `{"Authorization": bearer}`. `_prepare_request` builds the headers as `Content-Type`, then the Authorization header, then `custom_headers`. `custom_headers` is a static dict from config, so it suits a fixed `x-api-key`, for example.

**No built-in AWS IAM or SigV4 support** in `http` or `async_http`, at 1.54.0 or 1.55.0 (the source has no SigV4, botocore or `AWS4-HMAC-SHA256` code; `boto3` appears only in `msk_iam.py` and `amazon_datazone.py`). Signing an HTTP POST with SigV4 needs:
- the payload hash (`Hex(SHA256(body))`) in the canonical request;
- `host`, `x-amz-date` and, with temporary credentials (instance role, IRSA, ECS task role), `x-amz-security-token` among the signed headers, plus `content-type` when present;
- the method and path.

([IAM: Create a signed AWS API request](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html).) A `TokenProvider` sees none of the body, URL or time of the request, and cannot add `x-amz-*` headers. **So SigV4 is not feasible through a TokenProvider.**
- Lambda Function URLs with `AuthType: AWS_IAM`: "you must sign each HTTP request using AWS Signature Version 4 (SigV4) … Lambda processes the request only if the signatures match" ([urls-invocation](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html)). A stock client cannot do this.
- API Gateway IAM authorization needs SigV4 in the same way. **(inference; the API Gateway IAM page was not fetched)**

**What SigV4 would take (custom code, inference):**
- **A custom transport class.** For example, a subclass of `HttpTransport` that overrides `emit` or `_prepare_request` to sign with `botocore.auth.SigV4Auth` (service `lambda` or `execute-api`), selected by `type: <dotted path>`. Both dbt-ol and Airflow can load it through YAML or env, so the only change on the producer side is installing a small package next to openlineage-python. Its body must be signed exactly as sent. Gzip (`compression`) must therefore be applied before signing; the data-finops receiver refuses `Content-Encoding` with 415 anyway (spec 04).
- `HttpConfig.session` would accept a requests `Session` with SigV4 auth attached, but only programmatically. dbt-ol and the Airflow provider build the client themselves, so this route is closed to them.
- `amazon_datazone_api` with `endpointOverride` does send SigV4-signed requests through boto3. But it signs for service `datazone` and calls the DataZone `PostLineageEvent` path, so a Lambda (`lambda`) or API Gateway (`execute-api`) endpoint would not accept it. **(inference, unverified)**

**What works with stock auth on AWS:**
- `api_key` (static bearer) against any endpoint that checks the bearer itself: Lambda Function URL with `AuthType: NONE` plus the check in the function; API Gateway with a Lambda authorizer **(Lambda authorizer page not fetched)**; or our receiver behind an ALB.
- `custom_headers` for an API Gateway REST API key (`x-api-key`) **(header name from general knowledge, unverified)**.
- **`oauth2`** (1.54.0) against an **API Gateway HTTP API JWT authorizer**. The authorizer reads `Authorization`, with or without the `Bearer` prefix; validates `iss`, `aud` or `client_id`, `exp`, `nbf`, `iat` and `scope`; and accepts Amazon Cognito as issuer (`https://cognito-idp.<region>.amazonaws.com/<userPoolId>`) ([JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)). That gives each sender its own credentials, with no custom code on either side. That a Cognito app client issues client-credentials tokens of the expected shape was **not verified here**.
- **Private REST API.** API Gateway private APIs are "only callable from within an Amazon VPC" through an `execute-api` interface endpoint, with access restricted by `aws:SourceVpc` or `aws:SourceVpce` in the resource policy ([private APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html)). The network path is then restricted without per-request signing, so stock `http` plus `api_key` can reach it from inside the VPC. That the authorization type can be NONE when only the resource policy is used was **not verified on that page**. "Only REST APIs are supported" for private endpoints (same page): HTTP APIs, and so JWT authorizers, cannot be private.

## 3. File transport

From `transport/file.py`, at 1.54.0:
- **Naming:**
  - With `append: false` (the default), **each event goes to a new file** named `f"{log_file_path}-{datetime.now():%Y%m%d-%H%M%S.%f}.json"`, opened with mode `w`. For example, `log_file_path: s3://bucket/ol/dbt` gives `s3://bucket/ol/dbt-20261106-101500.123456.json`.
  - `datetime.now()` is naive local time.
  - Two events in the same microsecond from one prefix would overwrite each other **(inference)**. Separate senders should use separate prefixes.
  - With `debug_mode: true`, the name is `...-<yyMMddTHHMMSS.ffffff>-<job name>-<eventType>.json` and the JSON is indented.
- **Content:** `Serde.to_json(event) + "\n"`, which is `json.dumps(sort_keys=True)` with no indent: one line per event. So each default file is a one-line JSON Lines file, which the data-finops file ingester already accepts (JSON array or JSON Lines). A `debug_mode` file is a multi-line, pretty-printed single object, which is neither format.
- **`append: true`:** every event goes to `log_file_path` itself, opened with mode `a`, so the file is JSON Lines. The docstring warns that many object stores "do not support true append mode via fsspec … may raise an error or be emulated by rewriting the object". The docs page recommends `append: false` for cloud storage.
- **Remote paths:** if `log_file_path` contains `://` and is not `file://`, or `filesystem` is set, the transport needs fsspec (`openlineage-python[fsspec]`, `fsspec>=2023.1.0`) and opens with `fsspec.open(path, mode, **storage_options)`. `filesystem` can instead name a class, factory or instance by dotted path, with `fs_kwargs`. fsspec support was added in **1.38.0** (2025-10-01, CHANGELOG #3983). `file://` URIs were fixed in #4656 (2026-07-01). debug_mode was added in #4185.
- **S3** needs **s3fs** as well: fsspec's registry maps `s3` and `s3a` to `s3fs.S3FileSystem`, failing with "Install s3fs to access S3" ([fsspec registry.py](https://github.com/fsspec/filesystem_spec/blob/master/fsspec/registry.py), master, 2026-11-06). The OpenLineage `[fsspec]` extra does not pull s3fs in. s3fs uses the standard AWS credential chain (instance or task role), so S3 writes are IAM-authorised with no secret in OpenLineage config **(s3fs behaviour from general knowledge, unverified)**.
- **No S3 or object-storage transport exists** other than `file` with fsspec (section 1). The docs page shows the S3 case as `type: file` with `log_file_path: s3://my-bucket/lineage/events.jsonl`.

## 4. S3 or object-storage transport

There is no dedicated one in Python, at 1.54.0 or 1.55.0. The working route is `type: file`, `log_file_path: s3://<bucket>/<prefix>/<sender>`, `append: false`. It requires `openlineage-python[fsspec]` (since 1.38.0) plus `s3fs`, and gives one object per event. Its config keys are those in section 3. The Java client's `transports-s3` (1.23.0; config `bucketName`, `fileNamePrefix`, `endpoint`; it refuses to overwrite an existing key) is Java only.

## 5. Airflow provider 2.20.1

Read from `providers/openlineage/src/airflow/providers/openlineage/{conf.py,plugins/adapter.py,token_provider.py}` and `provider.yaml` at `e361f70`.
- **Order of precedence** (`OpenLineageAdapter.get_openlineage_config`):
  1. `[openlineage] config_conn_id`: a Generic Airflow connection whose extra holds the full client config, `{"transport": {...}}`.
  2. `[openlineage] config_path`: a YAML file.
  3. `[openlineage] transport`: a JSON string, via `conf.getjson`. The example in provider.yaml is `{"type": "http", "url": "http://localhost:5000", "endpoint": "api/v1/lineage"}`. As an env var it is `AIRFLOW__OPENLINEAGE__TRANSPORT`.
  4. If none is set, a bare `OpenLineageClient()`, so `openlineage.yml`, `OPENLINEAGE__*` and `OPENLINEAGE_URL` apply as in section 1.

  Any config found in steps 1 to 3 is passed as `OpenLineageClient(config=...)`, and the client still merges YAML and env underneath it.
- **Every client transport is available,** including `file` with `s3://`, `msk-iam`, `amazon_datazone_api` and custom dotted-path transports, since the provider passes the dict through. The provider requires `openlineage-python>=1.52.0` (its pyproject.toml).
- **Its own auth addition:** `auth: {"type": "airflow_connection_api_key", "conn_id": ..., "extra_key": ...}`. It reads the token from an Airflow connection's password or extra, and rewrites the block to `{"type": "api_key", "apiKey": ...}` before the client is built. It works inside composite transports (`resolve_airflow_connection_auth`). Added in 2.18.0 ("Read HTTP API key auth from Airflow connection", #66342). It is still a static bearer.
- **No AWS-specific transport, signing or credential support** in the files read. AWS reaches it only through the client transports above.
- **Provider 2.21.0** (2026-11-01): its changelog features list concerns event content (job names, task notes, dataset exclusion, parent macros). No transport or auth change is listed. Its code was not read.

## 6. Receivers that pull

None found among the standard receivers; every one read is push.
- Marquez receives lineage only by `POST /api/v1/lineage` ("Record a single lineage event", `spec/openapi.yml` on `main`, commit `180f37b`; latest release 0.50.0, 2024-10-24).
- The other targets the client ships are all push from the producer: DataZone (`PostLineageEvent`), Datadog, GCP Data Lineage, Kafka and MSK.

A pull design in this ecosystem means producers push to a buffer, here files or objects on S3 (or Kafka), and a consumer reads them later. No OpenLineage component offers a pull API on the producer side **(not exhaustively verified)**.

## Implications

| Pattern | Stock clients, no custom code? | Notes |
|---|---|---|
| **(a) HTTP + static bearer to a reachable URL** (public, or private through a VPC endpoint) | **Yes.** `http` with `auth.type: api_key`. dbt-ol through `OPENLINEAGE_URL` + `OPENLINEAGE_API_KEY` or YAML; Airflow through `[openlineage] transport` or `airflow_connection_api_key` | This is ADR 0024's token as it stands. One shared secret identifies no sender. A Lambda Function URL would have to be `AuthType: NONE`, with the token checked in our code. A private REST API restricted by `aws:SourceVpce` keeps the URL off the internet with the same client config. When the receiver is down, events are dropped (dbt-ol logs a warning) unless a `composite` with `file` is added. |
| **(a′) HTTP + OAuth2 client credentials to an API Gateway HTTP API JWT authorizer** | **Yes, with openlineage-python ≥ 1.54.0** (`auth.type: oauth2`) | Per-sender credentials and short-lived tokens, checked by API Gateway before our code runs. HTTP APIs cannot be private endpoints. Using Cognito client credentials for this is unverified. |
| **(b) HTTP to an IAM-authorised URL** (Function URL `AWS_IAM`, API Gateway IAM auth) | **No.** No SigV4 in `http` or `async_http`, and a TokenProvider can set only `Authorization` without seeing the body | Needs a small custom transport (`type: pkg.Class`, subclassing `HttpTransport` and signing with botocore) installed with every producer. Feasible, but it is code we would ship and maintain on the customers' side. |
| **(c) Producers write objects to S3; a scheduled job pulls them** | **Yes**, with `openlineage-python[fsspec]` + `s3fs` installed (no code) | `type: file`, `log_file_path: s3://bucket/prefix/<sender>`, `append: false`. Gives one single-line JSON object per event, which our JSON Lines ingest already reads. Auth is the producer's IAM role (`s3:PutObject` on the prefix), so there is no OpenLineage secret. Keep `debug_mode` off, and avoid `append: true` on S3. Can be combined with (a) through `composite`. Works for dbt-ol and Airflow alike. |

Also possible without custom code, but outside the receiver's HTTP path: `msk-iam` (Kafka with IAM). It means running MSK, which is heavier than M6 needs **(judgement)**.
