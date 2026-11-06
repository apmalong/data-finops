"""`GET /v1/allocations/{ref}/explanation` through FastAPI's test client (#110; spec 06
seam 2). No business numbers: the body must equal what the read model returns."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from fixture_database import ACCOUNT, fill
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.reporting import BillingLineDetail, Reporting

PROBLEM = "application/problem+json"
ROUTE = "/v1/allocations/{ref}/explanation"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api-explanation") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def _url(ref: str) -> str:
    return f"/v1/allocations/{quote(ref, safe='')}/explanation"


def test_every_allocation_is_explained_as_the_read_model_explains_it(
    client: TestClient, database: Path
) -> None:
    with Reporting(database) as reporting:
        refs = [item.ref for item in reporting.allocations(billing_account_id=ACCOUNT).items]
        expected = {ref: reporting.explanation(ref).model_dump(mode="json") for ref in refs}
    for ref in refs:
        response = client.get(_url(ref))
        assert (response.status_code, response.json()) == (200, expected[ref])
        valid_response("get", ROUTE, 200, response.json())


@pytest.mark.parametrize("ref", ["calc-secret-unknown#0", "secret-no-position"])
def test_an_unknown_reference_is_a_problem_that_does_not_echo_it(
    client: TestClient, ref: str
) -> None:
    response = client.get(_url(ref))
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert "secret" not in response.text
    valid_response("get", ROUTE, 404, response.json())


@pytest.mark.parametrize("detail", list(BillingLineDetail))
def test_the_app_serves_the_billing_line_at_its_configured_detail(
    database: Path, detail: BillingLineDetail
) -> None:
    with Reporting(database) as reporting:
        ref = reporting.allocations(billing_account_id=ACCOUNT).items[0].ref
        expected = reporting.explanation(ref, detail=detail).model_dump(mode="json")
    with TestClient(create_app(database, explanation_detail=detail)) as client:
        response = client.get(_url(ref))
    assert (response.status_code, response.json()) == (200, expected)
    assert response.json()["billing_line_detail"] == detail.value
    assert ("Tags" in response.json()["billing_line"]) is (detail is BillingLineDetail.FULL)
    valid_response("get", ROUTE, 200, response.json())
