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
