"""`GET /v1/costs/summary` reads a named Calculation Run or a source, as the other snapshot
reads do (#215; spec 06 "Which snapshot a read serves")."""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient
from fixture_database import ACCOUNT, fill, restate
from openapi_contract import valid_response

from data_finops.api import create_app

PROBLEM = "application/problem+json"
PATH = "/v1/costs/summary"
SOURCE = "aws-data-exports:FOCUS_1_2_AWS"


class Database(NamedTuple):
    path: Path
    first_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("api-summary") / "data-finops.sqlite"
    first = fill(path)
    restate(path)  # a later Export Snapshot is calculated: the first run is superseded
    return Database(path, first)


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(database.path), raise_server_exceptions=False) as opened:
        yield opened


def test_a_named_calculation_run_gives_the_summary_of_its_superseded_snapshot(
    client: TestClient, database: Database
) -> None:
    response = client.get(PATH, params={"calculation_run_id": database.first_run})
    assert response.status_code == 200
    (summary,) = response.json()["summaries"]
    assert summary["snapshot"]["calculation_run_id"] == database.first_run
    assert summary["snapshot"]["is_current"] is False
    assert response.json()["uncalculated_scopes"] == []
    valid_response("get", PATH, 200, response.json())


def test_without_a_run_the_summary_is_of_the_current_snapshot(
    client: TestClient, database: Database
) -> None:
    (summary,) = client.get(PATH).json()["summaries"]
    assert summary["snapshot"]["calculation_run_id"] != database.first_run
    assert summary["snapshot"]["is_current"] is True


def test_a_source_selects_the_scopes_of_that_source(client: TestClient) -> None:
    (summary,) = client.get(PATH, params={"source": SOURCE}).json()["summaries"]
    assert summary["scope"]["source"] == SOURCE
    assert client.get(PATH, params={"source": "another-source"}).json()["summaries"] == []


def test_an_unknown_calculation_run_is_not_found(client: TestClient) -> None:
    response = client.get(PATH, params={"calculation_run_id": "calc-secret-unknown"})
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert "secret" not in response.text
    valid_response("get", PATH, 404, response.json())


def test_a_named_run_with_scope_filters_is_a_problem(
    client: TestClient, database: Database
) -> None:
    response = client.get(
        PATH, params={"calculation_run_id": database.first_run, "billing_account_id": ACCOUNT}
    )
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert "calculation_run_id" in response.json()["detail"]
    valid_response("get", PATH, 400, response.json())
