"""The Deployment's Lambda entry points (spec 08; ADR 0026): thin wrappers over `collection`,
`api` and `public_demo` (spec 09), like `cli`. Each `make_*_handler` takes its clients and
environment, so tests hand it stubs; `collector`, `api`, `generator`, `simulator` and
`publisher` are what the functions are configured to call."""

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from data_finops.collection import AwsClients, CollectionSettings
from data_finops.collection.remote import (
    CachedDatabase,
    S3Reader,
    S3Writer,
    WriteConflict,
    run_remote_collection,
)
from data_finops.collection.run import FAILED, DimensionsUnavailable
from data_finops.phases import Phases

if TYPE_CHECKING:
    from data_finops.public_demo.dataset import DatasetFile
    from data_finops.reporting import LineFilter

COLLECTOR_SETTINGS = "DATA_FINOPS_COLLECTOR_SETTINGS"
"""The environment variable holding the collector's settings as JSON, with the keys of the
`collect` settings file (spec 08)."""
DEPLOYMENT_BUCKET = "DATA_FINOPS_BUCKET"
"""The environment variable naming the Deployment's bucket, for the API function and the
simulator, which writes to its Inbox."""
API_BUCKET = DEPLOYMENT_BUCKET
API_EXPLANATION_DETAIL = "DATA_FINOPS_EXPLANATION_DETAIL"
"""How much of the Billing Line the API's Cost Explanations show; `minimal` unless set."""
DEMO_BUCKET = "DATA_FINOPS_DEMO_BUCKET"
"""The environment variable naming the public demo's bucket, for the dataset generator and the
publisher."""

Handler = Callable[[Mapping[str, Any], Any], Any]


class CollectionFailed(RuntimeError):
    """The Collection Run failed, so the invocation fails and the scheduler retries it."""


class ApiMisconfigured(RuntimeError):
    """The API function's environment is wrong; the message names the variable."""


class GeneratorMisconfigured(RuntimeError):
    """The dataset generator's environment is wrong; the message names the variable."""


class PublisherMisconfigured(RuntimeError):
    """The publisher's environment is wrong; the message names the variable."""


class SimulatorMisconfigured(RuntimeError):
    """The simulator's environment or event is wrong; the message names the variable or field,
    never its value."""


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
                    settings,
                    clients=aws,
                    writer=cast(S3Writer, aws.s3),
                    scratch=Path(scratch),
                    phases=Phases("collector"),
                )
            except WriteConflict:
                raise CollectionFailed(
                    "the database object was changed by another writer; nothing was overwritten"
                ) from None
            except DimensionsUnavailable as error:  # names the key, never the file's content
                raise CollectionFailed(f"Cost Dimensions: {error}") from None
        print("\n".join(summary.printed_lines()))
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


def make_api_handler(
    s3: Callable[[], S3Reader], environ: Mapping[str, str], cache: Path
) -> Handler:
    """The read-only API behind API Gateway (spec 10): the JSON reads of the existing
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
    database = CachedDatabase(s3(), environ[API_BUCKET], cache / "data-finops.sqlite")
    # Mangum asks asyncio for the current event loop, which Python 3.12 deprecates when none
    # is set (an error from 3.14): give this execution environment one.
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = create_app(database.path, explanation_detail=detail, read_only=True)
    serve = Mangum(app, lifespan="off")

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        database.refresh()  # no object yet: no copy, and the app answers 503
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


def make_simulator_handler(
    clients: Callable[[], tuple[Any, Any]],
    environ: Mapping[str, str],
    *,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> Handler:
    """The public demo's simulated orchestrator (spec 09): one invocation runs the DAGs due at
    its scheduled time and writes their records to the Inbox of the Deployment's bucket. The
    schedule gives `scheduled_time`, so a retried invocation plans the same runs; without it,
    the time is now. `clients` gives the Athena and S3 clients."""
    from data_finops.public_demo.simulator import simulate

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        bucket = environ.get(DEPLOYMENT_BUCKET)
        if not bucket:
            raise SimulatorMisconfigured(f"{DEPLOYMENT_BUCKET} is not set")
        start = _scheduled_time(event, clock)
        athena, s3 = clients()
        summary = simulate(start, athena=athena, s3=s3, bucket=bucket, clock=clock, sleep=sleep)
        print(json.dumps(summary))
        return summary

    return handler


def _scheduled_time(event: Mapping[str, Any], clock: Callable[[], datetime]) -> datetime:
    given = event.get("scheduled_time")
    if given is None:
        return clock().replace(microsecond=0)
    try:
        moment = datetime.fromisoformat(str(given))
    except ValueError:
        raise SimulatorMisconfigured("scheduled_time is not an ISO 8601 time") from None
    if moment.tzinfo is None:
        raise SimulatorMisconfigured("scheduled_time has no time zone")
    return moment.astimezone(UTC)


def make_publisher_handler(
    s3: Callable[[], Any],
    environ: Mapping[str, str],
    *,
    clock: Callable[[], datetime],
    scratch: Path,
    lines: "LineFilter | None" = None,
) -> Handler:
    """The public demo's publisher (spec 09 §The publisher): when the collector writes the
    database object, it downloads it, as the API function does, and publishes the Demo
    Snapshot to the demo bucket. With no database object yet, it publishes nothing."""
    from data_finops.public_demo.publisher import publish

    def handler(event: Mapping[str, Any], context: Any) -> Any:
        buckets = {name: environ.get(name) for name in (DEPLOYMENT_BUCKET, DEMO_BUCKET)}
        for name, bucket in buckets.items():
            if not bucket:
                raise PublisherMisconfigured(f"{name} is not set")
        client = s3()
        scratch.mkdir(parents=True, exist_ok=True)
        path = scratch / "data-finops.sqlite"
        phases = Phases("publisher")
        with phases("download"):
            found = CachedDatabase(client, str(buckets[DEPLOYMENT_BUCKET]), path).refresh()
        if not found:
            print(json.dumps({"published": False}))
            return {"published": False}
        pointer = publish(
            path,
            s3=client,
            bucket=str(buckets[DEMO_BUCKET]),
            clock=clock,
            lines=lines,
            phases=phases,
        )
        print(json.dumps(pointer))
        return pointer

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


def publisher(event: Mapping[str, Any], context: Any) -> Any:
    """The publisher's handler: `data_finops.lambda_handlers.publisher`."""
    from data_finops.aws_clients import make_s3_client

    region = os.environ.get("AWS_REGION", "")
    return make_publisher_handler(
        lambda: make_s3_client(region),
        os.environ,
        clock=lambda: datetime.now(UTC),
        scratch=Path(tempfile.gettempdir()) / "data-finops-publisher",
    )(event, context)


def simulator(event: Mapping[str, Any], context: Any) -> Any:
    """The simulator's handler: `data_finops.lambda_handlers.simulator`."""
    import time

    from data_finops.aws_clients import make_athena_client, make_s3_client

    region = os.environ.get("AWS_REGION", "")
    return make_simulator_handler(
        lambda: (make_athena_client(region), make_s3_client(region)),
        os.environ,
        clock=lambda: datetime.now(UTC),
        sleep=time.sleep,
    )(event, context)


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
