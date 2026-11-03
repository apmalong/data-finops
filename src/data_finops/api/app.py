"""The FastAPI app over one database file (spec 06)."""

import hmac
from collections.abc import Awaitable, Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi import Path as PathParameter
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from data_finops.api.token import checked_token
from data_finops.contracts.allocation import AllocationCategory
from data_finops.contracts.billing import CostBasis
from data_finops.lineage.validation import MAX_EVENT_BYTES
from data_finops.reporting import (
    MAX_PAGE_SIZE,
    AllocationPage,
    AllocationReconciliation,
    AmbiguousScope,
    ConflictingFilters,
    CostExplanation,
    CostSummaries,
    DatabaseUnavailable,
    NotFound,
    Provenance,
    Reporting,
    RollupLevel,
    RollupPage,
    RootRunPage,
    RunLineage,
)
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


class _Encoded(Exception):
    """A request body with a Content-Encoding: compressed bodies are not accepted (#103)."""


_EVENT_CAP = f"{MAX_EVENT_BYTES // 2**20} MiB"


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
    401: _problem_doc("When a token is configured: no valid bearer token (ADR 0024)."),
    500: _problem_doc("An unexpected failure; the detail reveals nothing about it."),
    503: _problem_doc("The database cannot be opened as a current data-finops database."),
}

_SELECTION_PROBLEMS: dict[int | str, dict[str, Any]] = {
    **_READ_PROBLEMS,
    400: _problem_doc(
        "Invalid request, or filters matching several Snapshot Scopes: the detail names the"
        " fields to give, never their values."
    ),
    404: _problem_doc("No such Calculation Run, or no calculated Snapshot Scope matches."),
}
_HEALTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: _READ_PROBLEMS[401],
    503: {"model": Health, "description": "The database cannot be opened."},
}

_EXPLANATION_PROBLEMS: dict[int | str, dict[str, Any]] = {
    **_READ_PROBLEMS,
    404: _problem_doc("No such Allocation."),
}

_LINEAGE_PROBLEMS: dict[int | str, dict[str, Any]] = {
    **_READ_PROBLEMS,
    404: _problem_doc("No event reports or names that Workload Run."),
}

_RECEIVER_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {"model": LineageReceipt, "description": "Stored, or already stored (a duplicate)."},
    202: {"model": LineageUnsupported, "description": "A JobEvent or DatasetEvent: not stored."},
    400: _problem_doc("Rejected: the detail gives the kind and failing field, never content."),
    401: _READ_PROBLEMS[401],
    413: _problem_doc(f"The body is larger than one event may be ({_EVENT_CAP})."),
    415: _problem_doc("The body has a Content-Encoding; send the event uncompressed."),
    500: _READ_PROBLEMS[500],
    503: _READ_PROBLEMS[503],
}
_ONE_EVENT = {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}


def create_app(db: Path, *, token: str | None = None) -> FastAPI:
    """The API over the database file `db`; nothing is opened until a request needs it.

    With a `token`, every route, documentation included, needs `Authorization: Bearer
    <token>` (ADR 0024); without one, every route is open, for a loopback demo.
    """
    app = FastAPI(title="data-finops", version="v1", summary="Where the cloud bill went.")

    if token is not None:
        expected = checked_token(token).encode("utf-8")

        @app.middleware("http")
        async def require_token(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            # RFC 7235: a case-insensitive scheme, then one or more spaces, then the token.
            parts = request.headers.get("authorization", "").split()
            supplied = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
            if not hmac.compare_digest(supplied.encode("utf-8"), expected):
                refused = _problem_json(401, "Unauthorized", "A valid bearer token is required.")
                refused.headers["WWW-Authenticate"] = "Bearer"
                return refused
            return await call_next(request)

    def reporting() -> Iterator[Reporting]:
        with Reporting(db) as opened:
            yield opened

    @app.get("/health", responses=_HEALTH_RESPONSES)
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

    @app.get("/v1/allocations", responses=_SELECTION_PROBLEMS)
    def allocations(
        read_model: Annotated[Reporting, Depends(reporting)],
        calculation_run_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_account_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_period: date | None = None,
        source: Annotated[str | None, Query(max_length=256)] = None,
        target_id: Annotated[str | None, Query(max_length=256)] = None,
        category: AllocationCategory | None = None,
        billing_line_ref: Annotated[str | None, Query(max_length=256)] = None,
        cost_basis: CostBasis | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> AllocationPage:
        """One page of one Allocation Snapshot: a named Calculation Run's, or else the current
        one of the single Snapshot Scope that `billing_account_id`, `billing_period` and `source`
        select."""
        return read_model.allocations(
            calculation_run_id=calculation_run_id,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
            source=source,
            target_id=target_id,
            category=category,
            billing_line_ref=billing_line_ref,
            cost_basis=cost_basis,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/rollups", responses=_SELECTION_PROBLEMS)
    def rollups(
        read_model: Annotated[Reporting, Depends(reporting)],
        level: RollupLevel,
        calculation_run_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_account_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_period: date | None = None,
        source: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RollupPage:
        """What each Model Execution or Workload Run cost in one Allocation Snapshot, chosen as
        for `/v1/allocations`; kept apart because a Roll-up is a view, never an Allocation."""
        return read_model.rollups(
            level=level,
            calculation_run_id=calculation_run_id,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
            source=source,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/reconciliation", responses=_SELECTION_PROBLEMS)
    def reconciliation(
        read_model: Annotated[Reporting, Depends(reporting)],
        calculation_run_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_account_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_period: date | None = None,
        source: Annotated[str | None, Query(max_length=256)] = None,
        cost_basis: CostBasis | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> AllocationReconciliation:
        """One Allocation Snapshot's Allocations against the bill they partition: per Billing
        Line and Cost Basis, with per-currency totals over every line."""
        return read_model.reconciliation(
            calculation_run_id=calculation_run_id,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
            source=source,
            cost_basis=cost_basis,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/provenance", responses=_SELECTION_PROBLEMS)
    def provenance(
        read_model: Annotated[Reporting, Depends(reporting)],
        calculation_run_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_account_id: Annotated[str | None, Query(max_length=128)] = None,
        billing_period: date | None = None,
        source: Annotated[str | None, Query(max_length=256)] = None,
    ) -> Provenance:
        """How one Allocation Snapshot was made, chosen as for `/v1/allocations`: inputs,
        policies, the Export Snapshot it read, and the run that superseded it, if any."""
        return read_model.provenance(
            calculation_run_id=calculation_run_id,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
            source=source,
        )

    @app.get("/v1/lineage", responses=_LINEAGE_PROBLEMS)
    def lineage(
        read_model: Annotated[Reporting, Depends(reporting)],
        run_id: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RootRunPage | RunLineage:
        """Without `run_id`, the topmost Workload Runs (paged); with it, that run's lineage:
        its family, Data Assets, Lineage Edges and the Identity Links that reach its Model
        Executions and their Query Executions, each with its Link Outcome."""
        if run_id is None:
            return read_model.root_runs(limit=limit, offset=offset)
        return read_model.lineage(run_id)

    @app.get("/v1/allocations/{ref}/explanation", responses=_EXPLANATION_PROBLEMS)
    def explanation(
        read_model: Annotated[Reporting, Depends(reporting)],
        ref: Annotated[str, PathParameter(max_length=256)],
    ) -> CostExplanation:
        """The Cost Explanation of one Allocation, addressed `<calculation_run_id>#<position>`
        (the `#` sent as `%23`): Billing Line, Allocation, Evidence resolved, Roll-ups."""
        return read_model.explanation(ref)

    @app.exception_handler(NotFound)
    def not_found(request: Request, error: NotFound) -> JSONResponse:
        return _problem_json(404, "Not found", "Nothing matches the request.")

    @app.exception_handler(AmbiguousScope)
    def ambiguous_scope(request: Request, error: AmbiguousScope) -> JSONResponse:
        return _problem_json(
            400,
            "Several Snapshot Scopes match",
            "Narrow with billing_account_id, billing_period and source, or give"
            " calculation_run_id.",
        )

    @app.exception_handler(ConflictingFilters)
    def conflicting_filters(request: Request, error: ConflictingFilters) -> JSONResponse:
        return _problem_json(
            400,
            "Conflicting filters",
            "Give calculation_run_id or billing_account_id, billing_period and source, not both.",
        )

    @app.post(
        "/api/v1/lineage",
        status_code=201,
        response_model=LineageReceipt,
        responses=_RECEIVER_RESPONSES,
        openapi_extra={"requestBody": _ONE_EVENT},
    )
    async def receive_lineage(request: Request) -> Any:
        """Store one OpenLineage RunEvent, the default endpoint of OpenLineage's HTTP transport
        (spec 04). Screened exactly as one item of an event file.

        The one async endpoint (spec 06): it streams the body so an oversized one is refused
        at the cap instead of buffered (ADR 0024), then ingests in the thread pool."""
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
        return _problem_json(413, "Event too large", f"One event may be at most {_EVENT_CAP}.")

    @app.exception_handler(_Encoded)
    def encoded(request: Request, error: _Encoded) -> JSONResponse:
        return _problem_json(415, "Unsupported encoding", "Send the event uncompressed.")

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
    @app.exception_handler(UnusableDatabaseError)
    def unavailable(request: Request, error: Exception) -> JSONResponse:
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
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise _Encoded
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
