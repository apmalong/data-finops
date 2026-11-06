"""M5 exit criterion: the demo walkthrough passes as written (#119; spec 06, Exit).

Every step of `examples/demo/README.md` runs here as the reader runs it: inputs go in
through the command line (`main(argv)`), and what the reader sees is read back through
the API's test client, never from the stores.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from data_finops.api import create_app
from data_finops.cli import main

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ATHENA_SCOPE = ["--account-id", "123456789012", "--region", "us-east-1"]
SCOPE = {
    "billing_account_id": "123456789012",
    "billing_period": "2026-10-01",
    "source": "aws-data-exports:FOCUS_1_2_AWS",
}
DBT_RUN = "00000000-0000-4000-8000-000000000002"
INPUTS = [
    ["focus", FIXTURES / "focus" / "export-2026-10.json"],
    ["athena", FIXTURES / "athena" / "query-executions.json", *ATHENA_SCOPE],
    ["athena", FIXTURES / "athena" / "link-outcomes.json", *ATHENA_SCOPE],
    ["athena", FIXTURES / "athena" / "link-outcomes-restated.json", *ATHENA_SCOPE],
    ["openlineage", FIXTURES / "openlineage" / "events.json"],
    ["dbt", FIXTURES / "dbt" / "manifest.json"],
    ["dbt", FIXTURES / "dbt" / "run_results.json"],
]


def _run(db: Path, *argv: object) -> int:
    return main([*(str(a) for a in argv), "--db", str(db)])


def _walkthrough_database(folder: Path) -> Path:
    """Steps 1–2: ingest every fixture, then calculate."""
    database = folder / "data-finops.sqlite"
    assert [_run(database, "ingest", *step) for step in INPUTS] == [0] * len(INPUTS)
    assert _run(database, "calculate") == 0
    return database


@pytest.fixture(scope="module")
def read_only_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One database for the steps that only look (3–6)."""
    return _walkthrough_database(tmp_path_factory.mktemp("walkthrough"))


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A database of its own, for the steps that change it (7–8)."""
    return _walkthrough_database(tmp_path)


@pytest.fixture
def client(read_only_db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(read_only_db)) as test_client:
        yield test_client


@pytest.fixture
def own_client(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as test_client:
        yield test_client


def _usd(client: TestClient) -> dict[str, Any]:
    (summary,) = client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json()[
        "summaries"
    ]
    (usd,) = summary["currencies"]
    return usd  # type: ignore[no-any-return]


def test_step_3_the_bill_is_ten_dollars_and_reconciles(client: TestClient) -> None:
    usd = _usd(client)

    assert usd["billed"] == "10.00"
    assert usd["reconciliation_delta"] == "0.00"


def test_step_4_q001_is_six_dollars_metered_at_coverage_one_rolled_up_to_orders(
    client: TestClient,
) -> None:
    assert _usd(client)["allocated"] == [
        {
            "category": "direct",
            "confidence": "metered_estimate",
            "amount": "6.00",
            "coverage_min": "1",
            "coverage_max": "1",
        }
    ]
    direct = client.get(
        "/v1/allocations", params={**SCOPE, "cost_basis": "BilledCost", "category": "direct"}
    ).json()["items"]
    assert [(a["allocation"]["target_id"], a["allocation"]["amount"]) for a in direct] == [
        ("q-001", "6.00")
    ]
    explanation = client.get(f"/v1/allocations/{quote(direct[0]['ref'], safe='')}/explanation")
    assert explanation.status_code == 200
    assert "model.demo.orders" in explanation.text
    rollups = client.get("/v1/rollups", params={**SCOPE, "level": "model_execution"}).json()
    assert [
        (item["key"], total["amount"])
        for item in rollups["items"]
        for total in item["totals"]
        if total["cost_basis"] == "BilledCost"
    ] == [('["inv-001","model.demo.orders"]', "6.00")]


def test_step_5_customers_has_no_linked_query_only_an_ambiguous_claim(
    client: TestClient,
) -> None:
    lineage = client.get("/v1/lineage", params={"run_id": DBT_RUN}).json()

    (customers,) = [
        m for m in lineage["model_executions"] if m["node_id"] == "model.demo.customers"
    ]
    assert [
        (q["query_execution"]["query_execution_id"], q["outcome"]) for q in customers["queries"]
    ] == [("q-004", "ambiguous")]
    rollups = client.get("/v1/rollups", params={**SCOPE, "level": "model_execution"}).json()
    assert "model.demo.customers" not in str(rollups["items"])


def test_step_6_the_four_dollar_line_is_unallocated(client: TestClient) -> None:
    assert _usd(client)["unallocated"] == [{"reason": "ineligible", "amount": "4.00"}]


def test_step_7_running_everything_again_changes_nothing(
    db: Path, own_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    before = own_client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json()
    capsys.readouterr()

    assert [_run(db, "ingest", *step) for step in INPUTS] == [0] * len(INPUTS)
    assert _run(db, "calculate") == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0] == "focus: replay aws-focus-123456789012-2026-10-v1"
    assert out[-1].endswith(": replay " + before["summaries"][0]["snapshot"]["calculation_run_id"])
    assert own_client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json() == before


def test_step_8_a_restated_export_makes_a_new_current_allocation_snapshot_and_keeps_the_old(
    db: Path, own_client: TestClient
) -> None:
    old = own_client.get("/v1/provenance", params=SCOPE).json()

    restated = FIXTURES / "focus" / "export-2026-10-restated.json"
    assert _run(db, "ingest", "focus", restated) == 0
    assert _run(db, "calculate") == 0

    new = own_client.get("/v1/provenance", params=SCOPE).json()
    assert new["calculation_run_id"] != old["calculation_run_id"]
    assert new["export_snapshot"]["snapshot_id"] == "aws-focus-123456789012-2026-10-v2"
    assert _usd(own_client)["billed"] == "11.00"
    kept = own_client.get(
        "/v1/provenance", params={"calculation_run_id": old["calculation_run_id"]}
    ).json()
    assert kept["is_current"] is False
    assert kept["superseded_by"] == new["calculation_run_id"]
    assert kept["export_snapshot"]["snapshot_id"] == "aws-focus-123456789012-2026-10-v1"
