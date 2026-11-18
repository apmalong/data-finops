"""The dashboard's labels, amounts and tabs (#313), as the browser computes them: format.js
runs in Node. The expected values are written out by hand."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "src" / "data_finops" / "api" / "dashboard"
HARNESS = Path(__file__).with_name("format_harness.js")
RUN = "34695c53-ad28-4185-ad1b-51b87746d99b"
INVOCATION = "007c73e1-0239-43ef-bc7b-a0457ee7ec48"


def _call(name: str, *args: Any) -> Any:
    node = shutil.which("node")
    assert node, "the dashboard's format tests need Node, as the browser runs the same script"
    result = subprocess.run(  # noqa: S603 (a fixed command over files in this repository)
        [node, str(HARNESS), str(DASHBOARD / "format.js")],
        input=json.dumps([[name, *args]]),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    (answer,) = json.loads(result.stdout)
    return answer


def test_an_id_is_shown_by_its_first_eight_characters() -> None:
    assert _call("shortId", RUN) == "34695c53"
    assert _call("shortId", "q-001") == "q-001"


@pytest.mark.parametrize(
    ("amounts", "aligned"),
    [
        (["0.0029", "0.002875", "0.0023"], ["0.002900", "0.002875", "0.002300"]),
        (["6.00", "4"], ["6.00", "4.00"]),
        (["-1", "0.5"], ["-1.0", "0.5"]),
        (["6", "4"], ["6", "4"]),
    ],
)
def test_the_amounts_of_a_chart_get_one_number_of_decimals_and_keep_every_digit(
    amounts: list[str], aligned: list[str]
) -> None:
    assert _call("alignAmounts", amounts) == aligned


@pytest.mark.parametrize(
    ("amount", "currency", "shown"),
    [
        ("0.002900", "USD", "$0.002900"),
        ("-1.0", "USD", "-$1.0"),
        ("4.00", "EUR", "€4.00"),
        ("4.00", "CHF", "4.00 CHF"),  # a code with no symbol of its own
    ],
)
def test_an_amount_is_shown_with_its_currency(amount: str, currency: str, shown: str) -> None:
    assert _call("money", amount, currency) == shown


def test_a_model_execution_is_labelled_by_its_node_and_short_invocation() -> None:
    key = json.dumps([INVOCATION, "model.demo_shop.sessions"], separators=(",", ":"))
    assert _call("modelLabel", key) == {
        "name": "model.demo_shop.sessions",
        "detail": "invocation 007c73e1",
        "title": f"model.demo_shop.sessions · invocation {INVOCATION}",
    }


def test_a_workload_run_is_labelled_by_its_job_and_start() -> None:
    run = {"job_name": "daily_orders.dbt_run", "started_at": "2026-10-01T00:59:50Z"}
    assert _call("runLabel", RUN, run) == {
        "name": "daily_orders.dbt_run",
        "detail": "started 2026-10-01 00:59 UTC · run 34695c53",
        "title": f"daily_orders.dbt_run · run {RUN}",
    }


def test_a_workload_run_with_no_event_of_its_own_is_labelled_by_its_id() -> None:
    assert _call("runLabel", RUN, None) == {
        "name": "run 34695c53",
        "detail": "no event of its own",
        "title": f"run {RUN}",
    }


@pytest.mark.parametrize(
    ("fragment", "tab"),
    [
        ("", "summary"),
        ("#tab=models", "models"),
        ("#tab=runs", "runs"),
        ("#tab=dimensions", "dimensions"),
        ("#tab=nonsense", "summary"),
        ("#run=" + RUN, "runs"),  # a run's lineage opens on the Runs tab
        ("#explain=calc-1%230", None),  # an explanation keeps the tab that is open
    ],
)
def test_the_address_chooses_the_tab(fragment: str, tab: str | None) -> None:
    assert _call("tabFromHash", fragment) == tab
