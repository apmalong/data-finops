"""The Deployment's Lambda entry points (spec 08; ADR 0026): thin wrappers over `collection`,
`api` and `public_demo` (spec 09), like `cli`. Each `make_*_handler` takes its clients and
environment, so tests hand it stubs; `collector`, `api` and `generator` are what the functions
are configured to call."""

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from data_finops.collection import AwsClients, CollectionSettings
from data_finops.collection.remote import DATABASE_KEY, run_remote_collection
from data_finops.collection.run import FAILED, DimensionsUnavailable
from data_finops.providers.aws import S3Reader, S3Writer, WriteConflict, refresh_file

if TYPE_CHECKING:
    from data_finops.public_demo.dataset import DatasetFile

COLLECTOR_SETTINGS = "DATA_FINOPS_COLLECTOR_SETTINGS"
"""The environment variable holding the collector's settings as JSON, with the keys of the
`collect` settings file (spec 08)."""
API_BUCKET = "DATA_FINOPS_BUCKET"
"""The environment variable naming the Deployment's bucket, for the API function."""
API_EXPLANATION_DETAIL = "DATA_FINOPS_EXPLANATION_DETAIL"
"""How much of the Billing Line the API's Cost Explanations show; `minimal` unless set."""
DEMO_BUCKET = "DATA_FINOPS_DEMO_BUCKET"
"""The environment variable naming the public demo's bucket, for the dataset generator."""

Handler = Callable[[Mapping[str, Any], Any], Any]


class CollectionFailed(RuntimeError):
    """The Collection Run failed, so the invocation fails and the scheduler retries it."""


class ApiMisconfigured(RuntimeError):
    """The API function's environment is wrong; the message names the variable."""


class GeneratorMisconfigured(RuntimeError):
    """The dataset generator's environment is wrong; the message names the variable."""


def make_collector_handler(
    clients: Callable[[CollectionSettings], AwsClients], environ: Mapping[str, str]
) -> Handler:
    """The scheduled collector: one Collection Run over the database object. It returns the
    summary when everything was stored or some items were rejected (exit 0 or 1), and raises
    CollectionFailed when a source could not be read (exit 2), when another writer changed
    the database object, or when its settings are wrong."""

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        settings = _settings(environ)
        aws = clients(settings)
        with tempfile.TemporaryDirectory(prefix="data-finops-") as scratch:
            try:
                summary = run_remote_collection(
                    settings, clients=aws, writer=cast(S3Writer, aws.s3), scratch=Path(scratch)
                )
            except WriteConflict:
                raise CollectionFailed(
                    "the database object was changed by another writer; nothing was overwritten"
                ) from None
            except DimensionsUnavailable as error:  # names the key, never the file's content
                raise CollectionFailed(f"Cost Dimensions: {error}") from None
        print(summary.model_dump_json())
        for source in summary.sources.values():
            for rejected in source.rejected:
                print(f"  {rejected.item}: {rejected.reason}")
        if summary.exit_code == FAILED:
            raise CollectionFailed("a source could not be read (exit code 2); see the summary")
        return summary.model_dump(mode="json")

    return handler


def _settings(environ: Mapping[str, str]) -> CollectionSettings:
    """The collector's settings from its environment; CollectionFailed names the setting at
    fault, never its value."""
    if COLLECTOR_SETTINGS not in environ:
        raise CollectionFailed(f"{COLLECTOR_SETTINGS} is not set")
    try:
        values = json.loads(environ[COLLECTOR_SETTINGS])
    except ValueError:
        raise CollectionFailed(f"{COLLECTOR_SETTINGS} is not valid JSON") from None
    try:
        return CollectionSettings.from_mapping(values)
    except ValueError as error:
        raise CollectionFailed(f"{COLLECTOR_SETTINGS}: {error}") from None


class _CachedDatabase:
    """The API's copy of the database object, refreshed only when its ETag changes."""

    def __init__(self, s3: S3Reader, bucket: str, path: Path) -> None:
        self._s3, self._bucket, self.path = s3, bucket, path
        self._etag: str | None = None

    def refresh(self) -> None:
        """Bring the copy up to date; with no object yet there is no copy, and the app
        answers 503 as for any missing database."""
        self._etag = refresh_file(self._s3, self._bucket, DATABASE_KEY, self.path, self._etag)


def make_api_handler(
    s3: Callable[[], S3Reader], environ: Mapping[str, str], cache: Path
) -> Handler:
    """The read-only API behind the Function URL (ADR 0026): the JSON reads of the existing
    app over a cached copy of the database object. No receiver and no dashboard are mounted.
    ApiMisconfigured names a missing or wrong environment variable."""
    import asyncio

    from mangum import Mangum

    from data_finops.api import create_app
    from data_finops.reporting import BillingLineDetail

    if not environ.get(API_BUCKET):
        raise ApiMisconfigured(f"{API_BUCKET} is not set")
    try:
        detail = BillingLineDetail(environ.get(API_EXPLANATION_DETAIL, BillingLineDetail.MINIMAL))
    except ValueError:
        raise ApiMisconfigured(f"{API_EXPLANATION_DETAIL} must be minimal or full") from None
    cache.mkdir(parents=True, exist_ok=True)
    database = _CachedDatabase(s3(), environ[API_BUCKET], cache / "data-finops.sqlite")
    # Mangum asks asyncio for the current event loop, which Python 3.12 deprecates when none
    # is set (an error from 3.14): give this execution environment one.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = create_app(database.path, explanation_detail=detail, read_only=True)
    serve = Mangum(app, lifespan="off")

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        database.refresh()
        return serve(dict(event), context)

    return handler


def make_generator_handler(
    s3: Callable[[], Any],
    environ: Mapping[str, str],
    files: "Callable[[], Iterable[DatasetFile]] | None" = None,
) -> Handler:
    """The public demo's dataset generator (spec 09 §The demo dataset): a one-off invocation
    writes the dataset under `dataset/` in the demo bucket and returns how many files and
    bytes it wrote. A failed write fails the invocation; invoking it again writes the same
    files."""
    from data_finops.public_demo import dataset

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        bucket = environ.get(DEMO_BUCKET)
        if not bucket:
            raise GeneratorMisconfigured(f"{DEMO_BUCKET} is not set")
        written = dataset.upload(s3(), bucket, (files or dataset.generate)())
        print(json.dumps(written))
        return written

    return handler


def collector(event: Mapping[str, Any], context: Any) -> Any:
    """The collector function's handler: `data_finops.lambda_handlers.collector`."""
    from data_finops.aws_clients import make_aws_clients

    return make_collector_handler(make_aws_clients, os.environ)(event, context)


def generator(event: Mapping[str, Any], context: Any) -> Any:
    """The dataset generator's handler: `data_finops.lambda_handlers.generator`."""
    from data_finops.aws_clients import make_s3_client

    region = os.environ.get("AWS_REGION", "")
    return make_generator_handler(lambda: make_s3_client(region), os.environ)(event, context)


_api_handler: Handler | None = None


def api(event: Mapping[str, Any], context: Any) -> Any:
    """The API function's handler: `data_finops.lambda_handlers.api`. Built once per
    execution environment, so the cached copy outlives requests."""
    global _api_handler
    if _api_handler is None:
        from data_finops.aws_clients import make_s3_client

        _api_handler = make_api_handler(
            lambda: make_s3_client(os.environ.get("AWS_REGION", "")),
            os.environ,
            Path(tempfile.gettempdir()) / "data-finops",
        )
    return _api_handler(event, context)
