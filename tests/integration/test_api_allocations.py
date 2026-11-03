"""`GET /v1/allocations` through FastAPI's test client (#109; spec 06 seam 2).

No business numbers: a page must equal what the `reporting` read model returns.
"""

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
PATH = "/v1/allocations"


class Database(NamedTuple):
    path: Path
    first_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("api-allocations") / "data-finops.sqlite"
    first = fill(path)
    restate(path)
    return Database(path, first)


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(database.path), raise_server_exceptions=False) as opened:
        yield opened


def test_a_page_is_served_as_the_read_model_answers_it(
    client: TestClient, database: Database
) -> None:
    response = client.get(PATH, params={"limit": 3, "offset": 2})
    assert response.status_code == 200
    with Reporting(database.path) as reporting:
        expected = reporting.allocations(limit=3, offset=2)
    assert response.json() == expected.model_dump(mode="json")
    valid_response("get", PATH, 200, response.json())


def test_filters_and_a_superseded_snapshot_reach_the_read_model(
    client: TestClient, database: Database
) -> None:
    params = {
        "calculation_run_id": database.first_run,
        "target_id": "q-001",
        "category": "direct",
        "cost_basis": "BilledCost",
        "billing_line_ref": "aws-focus-123456789012-2026-10-v1#0",
    }
    response = client.get(PATH, params=params)
    with Reporting(database.path) as reporting:
        expected = reporting.allocations(
            calculation_run_id=database.first_run,
            target_id="q-001",
            category="direct",  # type: ignore[arg-type]
            cost_basis="BilledCost",  # type: ignore[arg-type]
            billing_line_ref="aws-focus-123456789012-2026-10-v1#0",
        )
    assert response.json() == expected.model_dump(mode="json")
    assert response.json()["total"] == 1
    scoped = client.get(
        PATH, params={"billing_account_id": ACCOUNT, "billing_period": "2026-10-01"}
    )
    assert scoped.json()["calculation_run_id"] != database.first_run


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": "1001"}, "limit"),
        ({"limit": "0"}, "limit"),
        ({"offset": "-1"}, "offset"),
        ({"category": "secret-category"}, "category"),
        ({"cost_basis": "secret-basis"}, "cost_basis"),
    ],
)
def test_invalid_paging_and_filters_are_problems_naming_the_field(
    client: TestClient, params: dict[str, str], field: str
) -> None:
    response = client.get(PATH, params=params)
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert field in response.json()["detail"] and "secret" not in response.text
    valid_response("get", PATH, 400, response.json())


def test_an_unknown_calculation_run_is_not_found(client: TestClient) -> None:
    response = client.get(PATH, params={"calculation_run_id": "calc-secret-unknown"})
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert "secret" not in response.text
    valid_response("get", PATH, 404, response.json())


def test_filters_matching_several_scopes_must_be_narrowed(tmp_path: Path) -> None:
    from fixture_database import EXPORT

    from data_finops.ledger import Ledger

    path = tmp_path / "data-finops.sqlite"
    fill(path)
    other = EXPORT.model_copy(
        update={
            "snapshot": EXPORT.snapshot.model_copy(
                update={"snapshot_id": "other-v1", "billing_account_id": "210987654321"}
            )
        }
    )
    with Ledger(path) as ledger:
        ledger.import_snapshot(other)
        ledger.store_allocations(
            other.snapshot.scope,
            calculation_run_id="calc-other",
            policy_version="test",
            input_digest="other",
            export_snapshot_id="other-v1",
            allocations=[],
        )
    with TestClient(create_app(path), raise_server_exceptions=False) as client:
        response = client.get(PATH)
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert "billing_account_id" in response.json()["detail"]
