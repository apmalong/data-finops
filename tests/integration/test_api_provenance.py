"""`GET /v1/provenance` through FastAPI's test client (#113; spec 06 seam 2). No business
numbers: the body must equal what the read model returns."""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient
from fixture_database import ACCOUNT, fill, restate
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.reporting import Reporting

PROBLEM = "application/problem+json"
PATH = "/v1/provenance"


class Database(NamedTuple):
    path: Path
    first_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("api-provenance") / "data-finops.sqlite"
    first = fill(path)
    restate(path)
    return Database(path, first)


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(database.path), raise_server_exceptions=False) as opened:
        yield opened


@pytest.mark.parametrize("selection", ["current", "superseded"])
def test_provenance_is_served_as_the_read_model_answers_it(
    client: TestClient, database: Database, selection: str
) -> None:
    run = None if selection == "current" else database.first_run
    account = ACCOUNT if run is None else None
    params = {"calculation_run_id": run} if run else {"billing_account_id": account}
    response = client.get(PATH, params=params)
    assert response.status_code == 200
    with Reporting(database.path) as reporting:
        expected = reporting.provenance(calculation_run_id=run, billing_account_id=account)
    assert response.json() == expected.model_dump(mode="json")
    valid_response("get", PATH, 200, response.json())


def test_an_unknown_run_or_conflicting_filters_are_problems(
    client: TestClient, database: Database
) -> None:
    unknown = client.get(PATH, params={"calculation_run_id": "calc-unknown"})
    conflicting = client.get(
        PATH, params={"calculation_run_id": database.first_run, "billing_account_id": ACCOUNT}
    )
    assert (unknown.status_code, conflicting.status_code) == (404, 400)
    for response, status in ((unknown, 404), (conflicting, 400)):
        assert response.headers["content-type"] == PROBLEM
        valid_response("get", PATH, status, response.json())
