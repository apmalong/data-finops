"""M5 exit criterion: the demo walkthrough passes as written (#119; spec 06, Exit).

Every claim `examples/demo/README.md` makes about what the reader sees is checked here as
the reader produces it: inputs go in through the command line (`main(argv)`), and what is
shown is read back through the API's test client, never from the stores. The dashboard
draws only what these answers hold; how it looks is checked by eye (spec 06).

`STEP_1` is the README's step 1, command for command; the `demo` command loads the same
fixtures from its own list, which the CLI tests hold.
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
ACCOUNT, REGION = "123456789012", "us-east-1"
SOURCE = "aws-data-exports:FOCUS_1_2_AWS"
SCOPE = {"billing_account_id": ACCOUNT, "billing_period": "2026-10-01", "source": SOURCE}
ATHENA = ["--account-id", ACCOUNT, "--region", REGION]
DAG_RUN, TASK_RUN, DBT_RUN = (f"00000000-0000-4000-8000-00000000000{n}" for n in range(3))
ORDERS = '["inv-001","model.demo.orders"]'
STEP_1 = [
    ["focus", FIXTURES / "focus" / "export-2026-10.json"],
    ["athena", FIXTURES / "athena" / "query-executions.json", *ATHENA],
    ["athena", FIXTURES / "athena" / "link-outcomes.json", *ATHENA],
    ["athena", FIXTURES / "athena" / "link-outcomes-restated.json", *ATHENA],
    ["openlineage", FIXTURES / "openlineage" / "events.json"],
    ["dbt", FIXTURES / "dbt" / "manifest.json"],
    ["dbt", FIXTURES / "dbt" / "run_results.json"],
]


def _data_finops(db: Path, *argv: object) -> int:
    """One `data-finops` command on `db`, as the README writes it."""
    return main([*(str(a) for a in argv), "--db", str(db)])


def _steps_1_and_2(db: Path) -> None:
    assert [_data_finops(db, "ingest", *command) for command in STEP_1] == [0] * len(STEP_1)
    assert _data_finops(db, "calculate") == 0


@pytest.fixture(scope="module")
def shared_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One database for the steps that only look (3–6)."""
    db = tmp_path_factory.mktemp("walkthrough") / "demo.sqlite"
    _steps_1_and_2(db)
    return db


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A database of its own, for the steps that change it (1–2, 7–8)."""
    return tmp_path / "demo.sqlite"


@pytest.fixture
def client(shared_db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(shared_db)) as test_client:
        yield test_client


def _summary(client: TestClient) -> dict[str, Any]:
    response = client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json()
    (summary,) = response["summaries"]
    return summary  # type: ignore[no-any-return]


def _usd(client: TestClient) -> dict[str, Any]:
    (usd,) = _summary(client)["currencies"]
    return usd  # type: ignore[no-any-return]


def _rollups(client: TestClient, level: str) -> dict[str, str]:
    """Billed Roll-ups at `level`, by key."""
    page = client.get("/v1/rollups", params={**SCOPE, "level": level}).json()
    return {
        item["key"]: total["amount"]
        for item in page["items"]
        for total in item["totals"]
        if total["cost_basis"] == "BilledCost"
    }


def _calculation_run(client: TestClient) -> str:
    return _summary(client)["snapshot"]["calculation_run_id"]  # type: ignore[no-any-return]


def test_steps_1_and_2_print_what_the_walkthrough_says(
    fresh_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _steps_1_and_2(fresh_db)

    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"focus: new aws-focus-{ACCOUNT}-2026-10-v1"
    assert out[-1].startswith(f"calculate: {ACCOUNT} 2026-10-01 {SOURCE}: new calc-")


def test_step_3_the_bill_is_ten_dollars_reconciled_under_its_calculation_run(
    client: TestClient,
) -> None:
    summary = _summary(client)
    (usd,) = summary["currencies"]

    assert usd["billed"] == "10.00"
    assert usd["reconciliation_delta"] == "0.00"
    snapshot = summary["snapshot"]
    assert snapshot["calculation_run_id"].startswith("calc-")
    assert snapshot["created_at"]
    assert "athena-billable-usage/1" in snapshot["policy_version"]


def test_step_4_q001_is_six_dollars_metered_explained_and_rolled_up(client: TestClient) -> None:
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
    (only,) = direct

    explanation = client.get(f"/v1/allocations/{quote(only['ref'], safe='')}/explanation").json()

    assert explanation["billing_line"]["BilledCost"] == "6.00"
    assert explanation["allocation"]["target_id"] == "q-001"
    assert explanation["query_execution"]["query_execution_id"] == "q-001"
    assert explanation["model_execution"]["node_id"] == "model.demo.orders"
    assert [run["run_id"] for run in explanation["workload_runs"]] == [DBT_RUN, TASK_RUN, DAG_RUN]
    billed = [r for r in explanation["rollups"] if r["cost_basis"] == "BilledCost"]
    assert {(r["key"], r["amount"]) for r in billed} == {
        (key, "6.00") for key in (ORDERS, DBT_RUN, TASK_RUN, DAG_RUN)
    }
    assert _rollups(client, "model_execution") == {ORDERS: "6.00"}
    assert _rollups(client, "workload_run") == {run: "6.00" for run in (DBT_RUN, TASK_RUN, DAG_RUN)}


def test_step_5_customers_has_no_linked_query_only_an_ambiguous_claim(
    client: TestClient,
) -> None:
    lineage = client.get("/v1/lineage", params={"run_id": DBT_RUN}).json()

    claims = {
        model["node_id"]: {
            (query["query_execution"]["query_execution_id"], query["outcome"])
            for query in model["queries"]
        }
        for model in lineage["model_executions"]
    }
    assert claims["model.demo.customers"] == {("q-004", "ambiguous")}
    assert ("q-001", "linked") in claims["model.demo.orders"]
    assert list(_rollups(client, "model_execution")) == [ORDERS]


def test_step_6_the_four_dollar_line_is_unallocated(client: TestClient) -> None:
    assert _usd(client)["unallocated"] == [{"reason": "ineligible", "amount": "4.00"}]


def test_step_7_running_everything_again_changes_nothing(
    fresh_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _steps_1_and_2(fresh_db)
    with TestClient(create_app(fresh_db)) as client:
        before = client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json()
        capsys.readouterr()

        _steps_1_and_2(fresh_db)

        out = capsys.readouterr().out.splitlines()
        assert out[0] == f"focus: replay aws-focus-{ACCOUNT}-2026-10-v1"
        assert out[-1].endswith(f": replay {_calculation_run(client)}")
        assert client.get("/v1/costs/summary", params={"cost_basis": "BilledCost"}).json() == before


def test_step_8_a_restated_export_makes_a_new_current_allocation_snapshot_and_keeps_the_old(
    fresh_db: Path,
) -> None:
    _steps_1_and_2(fresh_db)
    with TestClient(create_app(fresh_db)) as client:
        old_run = _calculation_run(client)

        restated = FIXTURES / "focus" / "export-2026-10-restated.json"
        assert _data_finops(fresh_db, "ingest", "focus", restated) == 0
        assert _data_finops(fresh_db, "calculate") == 0

        new = client.get("/v1/provenance", params=SCOPE).json()
        assert new["calculation_run_id"] != old_run
        assert new["export_snapshot"]["snapshot_id"] == f"aws-focus-{ACCOUNT}-2026-10-v2"
        assert _usd(client)["billed"] == "11.00"
        kept = client.get("/v1/provenance", params={"calculation_run_id": old_run}).json()
        assert (kept["is_current"], kept["superseded_by"]) == (False, new["calculation_run_id"])
        assert kept["export_snapshot"]["snapshot_id"] == f"aws-focus-{ACCOUNT}-2026-10-v1"
        old_allocations = client.get(
            "/v1/allocations", params={"calculation_run_id": old_run, "cost_basis": "BilledCost"}
        ).json()["items"]
        assert sorted(a["allocation"]["amount"] for a in old_allocations) == ["4.00", "6.00"]
