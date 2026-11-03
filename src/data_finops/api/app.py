"""The FastAPI app over one database file (spec 06)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from data_finops.contracts.billing import CostBasis
from data_finops.lineage.validation import MAX_EVENT_BYTES
from data_finops.reporting import CostSummaries, DatabaseUnavailable, Reporting
from data_finops.workload.store import IngestResult, UnusableDatabaseError, WorkloadStore

PROBLEM = "application/problem+json"


class Problem(BaseModel):
    """An RFC 9457 Problem Details body."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str


class LineageReceipt(BaseModel):
    """What storing one RunEvent changed, as the workload store's ingest result counts it."""

    accepted: int
    duplicates: int
    changed_copies: int


class LineageUnsupported(BaseModel):
    """A valid JobEvent or DatasetEvent: received, not stored (spec 04)."""

    unsupported: str


class _TooLarge(Exception):
    """A request body above the per-event cap."""


class Health(BaseModel):
    """Whether the database opens as a current data-finops database; a status, not an error."""

    status: Literal["ok", "unavailable"]


def _problem_doc(description: str) -> dict[str, Any]:
    """An OpenAPI response entry for a Problem Details body."""
    return {
        "description": description,
        "content": {PROBLEM: {"schema": Problem.model_json_schema()}},
    }


_READ_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: _problem_doc("Invalid request: the detail names the fields, never their values."),
    500: _problem_doc("An unexpected failure; the detail reveals nothing about it."),
    503: _problem_doc("The database cannot be opened as a current data-finops database."),
}

_RECEIVER_PROBLEMS: dict[int | str, dict[str, Any]] = {
    201: {"model": LineageReceipt, "description": "Stored, or already stored (a duplicate)."},
    202: {"model": LineageUnsupported, "description": "A JobEvent or DatasetEvent: not stored."},
    400: _problem_doc("Rejected: the detail gives the kind and failing field, never content."),
    413: _problem_doc("The body is larger than one event may be (1 MiB)."),
    500: _READ_PROBLEMS[500],
    503: _READ_PROBLEMS[503],
}
_ONE_EVENT = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}


def create_app(db: Path) -> FastAPI:
    """The API over the database file `db`; nothing is opened until a request needs it."""
    app = FastAPI(title="data-finops", version="v1", summary="Where the cloud bill went.")

    def reporting() -> Iterator[Reporting]:
        with Reporting(db) as opened:
            yield opened

    @app.get("/health", responses={503: {"model": Health}})
    def health(response: Response) -> Health:
        """Whether the database opens as a current data-finops database."""
        try:
            Reporting(db).close()
        except DatabaseUnavailable:
            response.status_code = 503
            return Health(status="unavailable")
        return Health(status="ok")

    @app.get("/v1/costs/summary", responses=_READ_PROBLEMS)
    def cost_summary(
        read_model: Annotated[Reporting, Depends(reporting)],
        cost_basis: CostBasis = CostBasis.BILLED_COST,
        billing_account_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_period: date | None = None,
    ) -> CostSummaries:
        """One Cost Summary per Snapshot Scope, for the scope's current Allocation Snapshot."""
        return read_model.cost_summary(
            cost_basis=cost_basis,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
        )

    @app.post(
        "/api/v1/lineage",
        status_code=201,
        response_model=LineageReceipt,
        responses=_RECEIVER_PROBLEMS,
        openapi_extra={"requestBody": _ONE_EVENT},
    )
    async def receive_lineage(request: Request) -> Any:
        """Store one OpenLineage RunEvent, the default endpoint of OpenLineage's HTTP transport
        (spec 04). Screened exactly as one item of an event file."""
        body = await _body_within_cap(request)
        result = await run_in_threadpool(_ingest_event, db, body)
        if result.rejected:
            return _problem_json(400, "Rejected event", result.rejected[0].reason)
        if result.unsupported:
            unsupported = LineageUnsupported(unsupported=result.unsupported[0].reason)
            return JSONResponse(unsupported.model_dump(), status_code=202)
        return LineageReceipt(
            accepted=result.accepted,
            duplicates=result.duplicates,
            changed_copies=result.changed_copies,
        )

    @app.exception_handler(_TooLarge)
    def too_large(request: Request, error: _TooLarge) -> JSONResponse:
        return _problem_json(413, "Event too large", "One event may be at most 1 MiB.")

    @app.exception_handler(UnusableDatabaseError)
    def unusable(request: Request, error: UnusableDatabaseError) -> JSONResponse:
        return _problem_json(503, "Database unavailable", "The database cannot be opened.")

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {".".join(map(str, e["loc"][1:])) or str(e["loc"][0]) for e in error.errors()}
        )
        return _problem_json(400, "Invalid request", f"Invalid value for: {', '.join(fields)}.")

    @app.exception_handler(HTTPException)
    def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _problem_json(error.status_code, str(error.detail), str(error.detail))

    @app.exception_handler(Exception)
    def unexpected(request: Request, error: Exception) -> JSONResponse:
        return _problem_json(500, "Internal error", "The request could not be completed.")

    @app.exception_handler(DatabaseUnavailable)
    def unavailable(request: Request, error: DatabaseUnavailable) -> JSONResponse:
        return _problem_json(503, "Database unavailable", "The database cannot be opened.")

    def openapi() -> dict[str, Any]:
        """The generated document without FastAPI's 422 entries: invalid input answers 400."""
        if app.openapi_schema is None:
            document = get_openapi(
                title=app.title, version=app.version, summary=app.summary, routes=app.routes
            )
            for operations in document["paths"].values():
                for operation in operations.values():
                    operation["responses"].pop("422", None)
            for unused in ("HTTPValidationError", "ValidationError"):
                document.get("components", {}).get("schemas", {}).pop(unused, None)
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


async def _body_within_cap(request: Request) -> bytes:
    """The request body, refused as soon as it exceeds one event's cap: a declared length is
    checked before anything is read, and a streamed body is never buffered past the cap."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_EVENT_BYTES:
        raise _TooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_EVENT_BYTES:
            raise _TooLarge
    return bytes(body)


def _ingest_event(db: Path, body: bytes) -> IngestResult:
    with WorkloadStore.open_existing(db) as store:
        return store.ingest_event(body)


def _problem_json(status: int, title: str, detail: str) -> JSONResponse:
    body = Problem(title=title, status=status, detail=detail).model_dump()
    return JSONResponse(body, status_code=status, media_type=PROBLEM)
