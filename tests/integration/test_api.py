"""The HTTP app through FastAPI's test client over a fixture database (#106; spec 06 seam 2).

No business numbers here: a response must equal what the `reporting` read model returns,
which its own tests check against the golden oracle.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill
from openapi_contract import valid_response

from data_finops.api import create_app
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"
PROBLEM = "application/problem+json"


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api") / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        for name in ("query-executions.json", "link-outcomes.json", "link-outcomes-restated.json"):
            store.ingest_query_history(
                FIXTURES / "athena" / name, account_id=ACCOUNT, region=REGION
            )
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        run_calculation(ledger, store, EXPORT.snapshot.scope)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def test_the_cost_summary_is_served_as_the_read_model_answers_it(
    client: TestClient, database: Path
) -> None:
    response = client.get("/v1/costs/summary")
    assert response.status_code == 200
    with Reporting(database) as reporting:
        assert response.json() == reporting.cost_summary().model_dump(mode="json")
    valid_response("get", "/v1/costs/summary", 200, response.json())


def test_filters_and_cost_basis_reach_the_read_model(client: TestClient, database: Path) -> None:
    response = client.get(
        "/v1/costs/summary",
        params={
            "cost_basis": "EffectiveCost",
            "billing_account_id": ACCOUNT,
            "billing_period": "2026-10-01",
        },
    )
    with Reporting(database) as reporting:
        expected = reporting.cost_summary(
            cost_basis=CostBasis.EFFECTIVE_COST,
            billing_account_id=ACCOUNT,
            billing_period=date(2026, 10, 1),
        )
    assert response.json() == expected.model_dump(mode="json")
    for unmatched in ({"billing_account_id": "0"}, {"billing_period": "2026-09-01"}):
        assert client.get("/v1/costs/summary", params=unmatched).json()["summaries"] == []


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"cost_basis": "InvoiceCost-secret"}, "cost_basis"),
        ({"billing_period": "2026-13-01-secret"}, "billing_period"),
    ],
)
def test_an_invalid_filter_is_a_problem_naming_the_field_only(
    client: TestClient, params: dict[str, str], field: str
) -> None:
    response = client.get("/v1/costs/summary", params=params)
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    problem = response.json()
    assert (problem["status"], problem["title"]) == (400, "Invalid request")
    assert field in problem["detail"] and "secret" not in response.text
    valid_response("get", "/v1/costs/summary", 400, problem)


def test_an_unknown_path_is_a_problem(client: TestClient) -> None:
    response = client.get("/v1/no-such-thing")
    assert (response.status_code, response.headers["content-type"]) == (404, PROBLEM)
    assert response.json()["status"] == 404


def test_health_reports_a_usable_database(client: TestClient) -> None:
    response = client.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    valid_response("get", "/health", 200, response.json())


def test_health_reports_a_file_that_is_not_a_database(tmp_path: Path) -> None:
    not_a_database = tmp_path / "notes.txt"
    not_a_database.write_text("not a database", encoding="utf-8")
    with TestClient(create_app(not_a_database), raise_server_exceptions=False) as client:
        health = client.get("/health")
        summary = client.get("/v1/costs/summary")
    assert (health.status_code, health.json()) == (503, {"status": "unavailable"})
    assert (summary.status_code, summary.headers["content-type"]) == (503, PROBLEM)
    assert "notes.txt" not in summary.text


def test_a_database_path_with_no_file_is_unavailable_and_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "typo.sqlite"
    with TestClient(create_app(missing), raise_server_exceptions=False) as client:
        health = client.get("/health")
        summary = client.get("/v1/costs/summary")
    assert (health.status_code, health.json()) == (503, {"status": "unavailable"})
    valid_response("get", "/health", 503, health.json())
    assert summary.status_code == 503
    valid_response("get", "/v1/costs/summary", 503, summary.json())
    assert not missing.exists()


def test_an_unexpected_failure_is_a_problem_that_reveals_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("SELECT secret FROM internals")

    monkeypatch.setattr(Reporting, "cost_summary", fail)
    response = client.get("/v1/costs/summary")
    assert (response.status_code, response.headers["content-type"]) == (500, PROBLEM)
    assert response.json()["status"] == 500
    assert "secret" not in response.text and "SELECT" not in response.text


def test_an_overlong_account_filter_is_refused(client: TestClient) -> None:
    response = client.get("/v1/costs/summary", params={"billing_account_id": "1" * 129})
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)


def test_no_response_carries_sql_or_database_details(client: TestClient) -> None:
    for path in ("/health", "/v1/costs/summary", "/v1/costs/summary?cost_basis=x", "/nope"):
        text = client.get(path).text.lower()
        assert not any(word in text for word in ("select ", "sqlite", "insert ", ".sqlite"))


def test_a_read_only_app_serves_reads_but_no_receiver_or_dashboard(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    fill(db)
    with TestClient(create_app(db, read_only=True)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/costs/summary").status_code == 200
        received = client.post("/api/v1/lineage", content=b"{}")
        assert received.status_code in (404, 405)
        assert client.get("/dashboard/").status_code == 404
        assert client.get("/", follow_redirects=False).status_code == 404
