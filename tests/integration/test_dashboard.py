"""The dashboard's smoke test (#117; spec 06: "files served; every API path they call is in
the OpenAPI document"). Its design is checked by eye against the design note."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill
from openapi_contract import OPENAPI

from data_finops.api import create_app

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "data_finops" / "api" / "dashboard"
FILES = ("index.html", "dashboard.css", "dashboard.js")


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("dashboard") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def test_the_dashboard_files_are_served(client: TestClient) -> None:
    page = client.get("/dashboard/")
    assert (page.status_code, page.headers["content-type"].split(";")[0]) == (200, "text/html")
    for name in FILES[1:]:
        assert client.get(f"/dashboard/{name}").status_code == 200


def test_the_root_leads_to_the_dashboard(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "/dashboard/")


def test_every_api_path_the_dashboard_calls_is_in_the_openapi_document() -> None:
    script = (DASHBOARD / "dashboard.js").read_text(encoding="utf-8")
    called = set(re.findall(r'"(/v1/[a-z/{}_]+)"', script))
    assert called and called <= set(OPENAPI["paths"])
    # The drilldown (#118) calls the Allocations, Cost Explanation and lineage reads too.
    assert {"/v1/allocations", "/v1/allocations/{ref}/explanation", "/v1/lineage"} <= called


def test_the_dashboard_needs_no_library_and_no_network_beyond_its_api() -> None:
    for name in FILES:
        text = (DASHBOARD / name).read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, name
        assert "<script src" not in text.replace('<script src="dashboard.js"', ""), name


def _spec_06_rules() -> list[str]:
    spec = (ROOT / "docs" / "specs" / "06-api-and-dashboard.md").read_text(encoding="utf-8")
    section = spec.split("## Dashboard", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- (.+)$", section, flags=re.M)


def test_the_design_note_quotes_and_ticks_every_spec_06_dashboard_rule() -> None:
    note = (DASHBOARD / "DESIGN.md").read_text(encoding="utf-8")
    ticked = set(re.findall(r"^- \[x\] (.+)$", note, flags=re.M))
    rules = _spec_06_rules()
    assert len(rules) == 8
    assert [rule for rule in rules if rule not in ticked] == []
    assert re.findall(r"^- \[ \] ", note, flags=re.M) == []


def test_with_a_token_the_dashboard_is_guarded_like_every_route(database: Path) -> None:
    token = "s3cret-token-value-0123456789"
    with TestClient(create_app(database, token=token), raise_server_exceptions=False) as client:
        assert client.get("/dashboard/").status_code == 401
        authorised = {"authorization": f"Bearer {token}"}
        assert client.get("/dashboard/", headers=authorised).status_code == 200
