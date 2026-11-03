"""The bearer token on every route (#108; ADR 0024; spec 06 seam 2)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from fixture_database import fill
from openapi_contract import valid_response

from data_finops.api import TOKEN_VARIABLE, create_app, read_token
from data_finops.workload.store import WorkloadStore

TOKEN = "s3cret-token-value-0123456789"
PROBLEM = "application/problem+json"
EVENT = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "openlineage" / "events.json").read_text(
        encoding="utf-8"
    )
)[0]
ROUTES = [
    ("get", "/health"),
    ("get", "/v1/costs/summary"),
    ("get", "/v1/allocations"),
    ("get", "/openapi.json"),
    ("post", "/api/v1/lineage"),
]


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("token") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database, token=TOKEN), raise_server_exceptions=False) as opened:
        yield opened


def _call(client: TestClient, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    if method == "post":
        return client.post(path, json=EVENT, headers=headers)
    return client.get(path, headers=headers)


def _reveals(response: httpx.Response, secret: str) -> bool:
    return secret in response.text or any(secret in value for value in response.headers.values())


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer wrong-token-value-012345", TOKEN, f"Basic {TOKEN}", "Bearer", "Bearer "],
    ids=["missing", "wrong", "no-scheme", "basic", "scheme-only", "scheme-and-space"],
)
def test_every_route_refuses_a_missing_or_wrong_token(
    client: TestClient, method: str, path: str, authorization: str | None
) -> None:
    headers = {} if authorization is None else {"authorization": authorization}
    response = _call(client, method, path, headers)
    assert (response.status_code, response.headers["content-type"]) == (401, PROBLEM)
    assert response.headers["www-authenticate"] == "Bearer"
    assert not _reveals(response, TOKEN)


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "authorization",
    [f"Bearer {TOKEN}", f"bearer {TOKEN}", f"BEARER  {TOKEN} "],
    ids=["exact", "lowercase-scheme", "extra-spaces"],
)
def test_every_route_answers_with_the_token(
    client: TestClient, method: str, path: str, authorization: str
) -> None:
    response = _call(client, method, path, {"authorization": authorization})
    assert response.status_code in (200, 201)
    assert not _reveals(response, TOKEN)


def test_a_refusal_is_documented_and_valid(client: TestClient) -> None:
    response = client.get("/v1/costs/summary")
    valid_response("get", "/v1/costs/summary", 401, response.json())


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_without_a_token_configured_every_route_is_open(
    database: Path, method: str, path: str
) -> None:
    with TestClient(create_app(database), raise_server_exceptions=False) as client:
        assert _call(client, method, path, {}).status_code in (200, 201)


@pytest.mark.parametrize("token", ["", "short", "  " + TOKEN, TOKEN + "\n"])
def test_the_app_refuses_an_empty_short_or_padded_token(database: Path, token: str) -> None:
    with pytest.raises(ValueError):
        create_app(database, token=token)


def test_the_token_is_never_stored_with_a_received_event(
    client: TestClient, database: Path
) -> None:
    response = client.post(
        "/api/v1/lineage", json=EVENT, headers={"authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 201
    with WorkloadStore(database) as store:
        assert not any(TOKEN in stored for stored in store.stored_events(EVENT["run"]["runId"]))


def test_the_token_never_reaches_a_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.get("/v1/costs/summary", headers={"authorization": f"Bearer {TOKEN}"})
        client.get(
            "/v1/costs/summary", headers={"authorization": "Bearer wrong-token-value-012345"}
        )
    assert TOKEN not in caplog.text and "wrong-token-value" not in caplog.text


def test_the_token_comes_from_the_environment_or_a_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(f"{TOKEN}\n", encoding="utf-8")
    assert read_token(environ={}, file=None) is None
    assert read_token(environ={TOKEN_VARIABLE: TOKEN}, file=None) == TOKEN
    assert read_token(environ={}, file=token_file) == TOKEN


@pytest.mark.parametrize("value", ["", "   ", "short"])
def test_an_empty_or_short_token_is_refused(value: str) -> None:
    with pytest.raises(ValueError) as refused:
        read_token(environ={TOKEN_VARIABLE: value}, file=None)
    assert "16 characters" in str(refused.value)


def test_a_token_file_holding_only_whitespace_is_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_token(environ={}, file=token_file)


def test_a_token_in_both_places_is_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    with pytest.raises(ValueError) as refused:
        read_token(environ={TOKEN_VARIABLE: TOKEN}, file=token_file)
    assert TOKEN not in str(refused.value)
