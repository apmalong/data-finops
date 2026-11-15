"""`GET /v1/rollups/dimensions/{name}` through FastAPI's test client (#222; spec 06 seam 2).
No business numbers: a page must equal what the read model returns."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import ACCOUNT, fill
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.contracts.dimensions import CostDimensions
from data_finops.reporting import Reporting

PROBLEM = "application/problem+json"
DIMENSIONS = CostDimensions.from_mapping(
    {"dimensions": {"team": {"sources": ["workgroup_map"], "workgroup_map": {"demo": "analytics"}}}}
)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api-dimensions") / "data-finops.sqlite"
    fill(path, dimensions=DIMENSIONS)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def test_a_page_is_served_as_the_read_model_answers_it(client: TestClient, database: Path) -> None:
    response = client.get("/v1/rollups/dimensions/team", params={"billing_account_id": ACCOUNT})
    assert response.status_code == 200
    with Reporting(database) as reporting:
        expected = reporting.dimension_rollups(name="team", billing_account_id=ACCOUNT)
    assert response.json() == expected.model_dump(mode="json")
    valid_response("get", "/v1/rollups/dimensions/{name}", 200, response.json())


def test_an_unknown_dimension_is_a_problem_that_does_not_echo_it(client: TestClient) -> None:
    response = client.get("/v1/rollups/dimensions/secret-name")
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert "secret" not in response.text
    valid_response("get", "/v1/rollups/dimensions/{name}", 404, response.json())
