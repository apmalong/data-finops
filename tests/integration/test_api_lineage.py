"""`GET /v1/lineage` through FastAPI's test client (#114; spec 06 seam 2). No business
numbers: the body must equal what the read model returns."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.reporting import Reporting

PROBLEM = "application/problem+json"
PATH = "/v1/lineage"
RUNS = [f"00000000-0000-4000-8000-00000000000{n}" for n in range(4)]


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api-lineage") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def test_the_root_runs_are_served_as_the_read_model_answers_them(
    client: TestClient, database: Path
) -> None:
    response = client.get(PATH)
    assert response.status_code == 200
    with Reporting(database) as reporting:
        assert response.json() == reporting.root_runs().model_dump(mode="json")
    valid_response("get", PATH, 200, response.json())


@pytest.mark.parametrize("run_id", RUNS)
def test_each_runs_lineage_is_served_as_the_read_model_answers_it(
    client: TestClient, database: Path, run_id: str
) -> None:
    response = client.get(PATH, params={"run_id": run_id})
    assert response.status_code == 200
    with Reporting(database) as reporting:
        assert response.json() == reporting.lineage(run_id).model_dump(mode="json")
    valid_response("get", PATH, 200, response.json())


def test_an_unknown_run_is_a_problem_that_does_not_echo_it(client: TestClient) -> None:
    response = client.get(PATH, params={"run_id": "secret-run"})
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert "secret" not in response.text
    valid_response("get", PATH, 404, response.json())


def test_invalid_paging_is_a_problem_naming_the_field(client: TestClient) -> None:
    response = client.get(PATH, params={"limit": "0"})
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert "limit" in response.json()["detail"]
