"""`GET /v1/reconciliation` through FastAPI's test client (#112; spec 06 seam 2). No business
numbers: a page must equal what the read model returns."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import ACCOUNT, fill
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.contracts.billing import CostBasis
from data_finops.reporting import Reporting

PROBLEM = "application/problem+json"
PATH = "/v1/reconciliation"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api-reconciliation") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def test_a_page_is_served_as_the_read_model_answers_it(client: TestClient, database: Path) -> None:
    params = {"billing_account_id": ACCOUNT, "cost_basis": "EffectiveCost", "limit": 1, "offset": 1}
    response = client.get(PATH, params=params)
    assert response.status_code == 200
    with Reporting(database) as reporting:
        expected = reporting.reconciliation(
            billing_account_id=ACCOUNT, cost_basis=CostBasis.EFFECTIVE_COST, limit=1, offset=1
        )
    assert response.json() == expected.model_dump(mode="json")
    valid_response("get", PATH, 200, response.json())


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"cost_basis": "secret-basis"}, "cost_basis"),
        ({"limit": "0"}, "limit"),
        ({"offset": "-1"}, "offset"),
    ],
)
def test_invalid_filters_and_paging_are_problems_naming_the_field(
    client: TestClient, params: dict[str, str], field: str
) -> None:
    response = client.get(PATH, params=params)
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert field in response.json()["detail"] and "secret" not in response.text
    valid_response("get", PATH, 400, response.json())


def test_an_unknown_calculation_run_is_not_found(client: TestClient) -> None:
    response = client.get(PATH, params={"calculation_run_id": "calc-unknown"})
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    valid_response("get", PATH, 404, response.json())
