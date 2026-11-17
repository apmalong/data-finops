"""AWS clients for the entry points (`cli`, `lambda_handlers`; spec 08). Only here is boto3
imported: everything else is handed a client, so tests stub it and nothing else needs the
`aws` extra (the import-boundary test)."""

from typing import Any

from data_finops.collection import AwsClients, CollectionSettings


class ClientsUnavailable(RuntimeError):
    """No client could be made: the extra is missing, or the profile or region is wrong.
    The message never names the profile."""


def make_aws_clients(settings: CollectionSettings) -> AwsClients:
    """S3 and Athena clients for the settings' profile and region; boto3 is the `aws`
    extra. Making them calls nothing: the first request is the Collection Run's own."""
    session, config = _aws_session(settings.profile, settings.region)
    return AwsClients(
        s3=session.client("s3", config=config), athena=session.client("athena", config=config)
    )


def make_s3_client(region: str) -> Any:
    """An S3 client for the region, configured as `make_aws_clients` configures its own."""
    session, config = _aws_session(None, region)
    return session.client("s3", config=config)


def make_athena_client(region: str) -> Any:
    """An Athena client for the region, configured as `make_aws_clients` configures its own."""
    session, config = _aws_session(None, region)
    return session.client("athena", config=config)


def _aws_session(profile: str | None, region: str) -> tuple[Any, Any]:
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError
    except ImportError:
        raise ClientsUnavailable("AWS clients need the aws extra: uv sync --extra aws") from None
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
    except BotoCoreError as error:  # its message may name the profile: say what kind only
        raise ClientsUnavailable(
            f"cannot make an AWS client ({type(error).__name__}): check the profile and region"
        ) from None
    # botocore's standard retries: throttling and transient errors backed off with jitter,
    # at most 5 attempts; then the source fails and the scheduler retries the run (Q44).
    return session, Config(retries={"mode": "standard", "total_max_attempts": 5})


def signed_headers(url: str, *, region: str, profile: str) -> dict[str, str]:
    """The headers that sign a GET of `url` with SigV4, for the profile's credentials. The
    Deployment's API is a Lambda Function URL with IAM auth (ADR 0026), so the service is
    `lambda`. When API Gateway replaces it (#290), the service becomes `execute-api`. Signing
    calls nothing. An expired or missing login fails here, without naming the profile."""
    session, _ = _aws_session(profile, region)
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.exceptions import BotoCoreError

    try:
        credentials = session.get_credentials()
        frozen = None if credentials is None else credentials.get_frozen_credentials()
    except BotoCoreError as error:
        raise ClientsUnavailable(
            f"cannot get AWS credentials ({type(error).__name__}): sign in to the profile again"
        ) from None
    if frozen is None:
        raise ClientsUnavailable("the profile has no AWS credentials")
    request = AWSRequest(method="GET", url=url)
    SigV4Auth(frozen, "lambda", region).add_auth(request)
    return dict(request.headers.items())
