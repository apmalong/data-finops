"""The FastAPI app over one database file (spec 06)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from data_finops.contracts.billing import CostBasis
from data_finops.reporting import CostSummaries, DatabaseUnavailable, Reporting

PROBLEM = "application/problem+json"


class Problem(BaseModel):
    """An RFC 9457 Problem Details body."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str


class Health(BaseModel):
    status: Literal["ok", "unavailable"]


def _problem_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {PROBLEM: {"schema": Problem.model_json_schema()}},
    }


_READ_PROBLEMS: dict[int | str, dict[str, Any]] = {
    400: _problem_response("Invalid request: the detail names the fields, never their values."),
    503: _problem_response("The database cannot be opened as a current data-finops database."),
}


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
        billing_account_id: str | None = None,
        billing_period: date | None = None,
    ) -> CostSummaries:
        """One Cost Summary per Snapshot Scope, for the scope's current Allocation Snapshot."""
        return read_model.cost_summary(
            cost_basis=cost_basis,
            billing_account_id=billing_account_id,
            billing_period=billing_period,
        )

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted(
            {".".join(map(str, e["loc"][1:])) or str(e["loc"][0]) for e in error.errors()}
        )
        return _problem(400, "Invalid request", f"Invalid value for: {', '.join(fields)}.")

    @app.exception_handler(HTTPException)
    def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _problem(error.status_code, str(error.detail), str(error.detail))

    @app.exception_handler(DatabaseUnavailable)
    def unavailable(request: Request, error: DatabaseUnavailable) -> JSONResponse:
        return _problem(503, "Database unavailable", "The database cannot be opened.")

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


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    body = Problem(title=title, status=status, detail=detail).model_dump()
    return JSONResponse(body, status_code=status, media_type=PROBLEM)
