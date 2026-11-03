"""The OpenLineage receiver, `POST /api/v1/lineage`, through FastAPI's test client (#107;
spec 04; spec 06 seam 2).

What is stored is read back through the workload store's public interface and compared with
the same events ingested from their file; the store's own tests cover screening itself.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from data_finops.api import create_app
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "fixtures" / "openlineage" / "events.json"
INVALID = ROOT / "fixtures" / "openlineage" / "invalid" / "mixed.jsonl"
OPENAPI = json.loads((ROOT / "schemas" / "openapi.json").read_text(encoding="utf-8"))
RECEIVER = "/api/v1/lineage"
PROBLEM = "application/problem+json"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "data-finops.sqlite"
    Ledger(path).close()
    WorkloadStore(path).close()
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database), raise_server_exceptions=False) as opened:
        yield opened


def _events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = json.loads(EVENTS.read_text(encoding="utf-8"))
    return events


def _invalid_line(number: int) -> bytes:
    return INVALID.read_bytes().split(b"\n")[number - 1]


def _valid_response(status: int, body: Any) -> None:
    content = OPENAPI["paths"][RECEIVER]["post"]["responses"][str(status)]["content"]
    schema = next(iter(content.values()))["schema"]
    Draft202012Validator({**schema, "components": OPENAPI["components"]}).validate(body)


def test_events_posted_one_by_one_are_stored_as_from_their_file(
    client: TestClient, database: Path, tmp_path: Path
) -> None:
    for event in _events():
        response = client.post(RECEIVER, json=event)
        assert (response.status_code, response.json()["accepted"]) == (201, 1)
        _valid_response(201, response.json())
    with WorkloadStore(tmp_path / "file.sqlite") as from_file:
        from_file.ingest_events(EVENTS)
        with WorkloadStore(database) as received:
            for run_id in {e["run"]["runId"] for e in _events()}:
                assert received.stored_events(run_id) == from_file.stored_events(run_id)


def test_the_same_event_again_is_a_duplicate(client: TestClient) -> None:
    client.post(RECEIVER, json=_events()[0])
    response = client.post(RECEIVER, json=_events()[0])
    assert (response.status_code, response.json()) == (
        201,
        {"accepted": 0, "duplicates": 1, "changed_copies": 0},
    )


def test_a_job_event_is_accepted_as_unsupported(client: TestClient) -> None:
    response = client.post(
        RECEIVER, content=_invalid_line(6), headers={"content-type": "application/json"}
    )
    assert (response.status_code, response.json()) == (202, {"unsupported": "unsupported JobEvent"})
    _valid_response(202, response.json())


@pytest.mark.parametrize(
    ("line", "reason"),
    [(2, "not valid JSON"), (3, "invalid RunEvent: run/runId (format)")],
)
def test_a_rejected_event_is_a_problem_giving_its_reason_not_its_content(
    client: TestClient, line: int, reason: str
) -> None:
    response = client.post(
        RECEIVER, content=_invalid_line(line), headers={"content-type": "application/json"}
    )
    assert (response.status_code, response.headers["content-type"]) == (400, PROBLEM)
    assert response.json()["detail"] == reason
    assert "not-a-uuid-secret-value" not in response.text
    _valid_response(400, response.json())


def _oversized() -> bytes:
    event = _events()[0]
    event["run"]["facets"]["padding"] = {"_producer": "x", "_schemaURL": "x", "data": "x" * 2**20}
    return json.dumps(event).encode("utf-8")


def test_a_body_over_one_mebibyte_is_refused_before_it_is_read(client: TestClient) -> None:
    response = client.post(
        RECEIVER, content=_oversized(), headers={"content-type": "application/json"}
    )
    assert (response.status_code, response.headers["content-type"]) == (413, PROBLEM)
    _valid_response(413, response.json())


def test_a_streamed_body_over_one_mebibyte_is_refused(client: TestClient) -> None:
    body = _oversized()
    chunks = (body[i : i + 65536] for i in range(0, len(body), 65536))
    response = client.post(RECEIVER, content=chunks, headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_a_lying_content_length_cannot_slip_a_large_body_past_the_cap(client: TestClient) -> None:
    response = client.post(
        RECEIVER,
        content=_oversized(),
        headers={"content-type": "application/json", "content-length": "10"},
    )
    assert response.status_code in (400, 413)
    assert response.status_code != 201


def test_events_are_refused_when_there_is_no_database(tmp_path: Path) -> None:
    missing = tmp_path / "typo.sqlite"
    with TestClient(create_app(missing), raise_server_exceptions=False) as client:
        response = client.post(RECEIVER, json=_events()[0])
    assert (response.status_code, response.headers["content-type"]) == (503, PROBLEM)
    assert not missing.exists()


def test_events_are_refused_when_the_file_is_not_a_database(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a database", encoding="utf-8")
    with TestClient(create_app(notes), raise_server_exceptions=False) as client:
        response = client.post(RECEIVER, json=_events()[0])
    assert (response.status_code, response.headers["content-type"]) == (503, PROBLEM)
    assert notes.read_text(encoding="utf-8") == "not a database"


def test_a_declared_length_over_the_cap_is_refused_without_reading_the_body(
    client: TestClient,
) -> None:
    response = client.post(
        RECEIVER,
        content=json.dumps(_events()[0]).encode("utf-8"),
        headers={"content-type": "application/json", "content-length": str(2**20 + 1)},
    )
    assert response.status_code == 413
