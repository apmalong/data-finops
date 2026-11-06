"""Concurrent reads, as a browser loading the dashboard makes them (bug found in the M5
manual browser pass, #119).

FastAPI runs a sync dependency and a sync endpoint in its threadpool, not necessarily in
the same worker thread; an SQLite connection opened in one thread and used in another
fails. One request at a time hides it, because a free worker is usually reused.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
from fixture_database import fill

from data_finops.api import create_app

SCOPE = {
    "billing_account_id": "123456789012",
    "billing_period": "2026-10-01",
    "source": "aws-data-exports:FOCUS_1_2_AWS",
}
DASHBOARD_LOAD = [
    ("/v1/costs/summary", {"cost_basis": "BilledCost"}),
    ("/v1/rollups", {**SCOPE, "level": "model_execution"}),
    ("/v1/rollups", {**SCOPE, "level": "workload_run"}),
    ("/v1/allocations", {**SCOPE, "cost_basis": "BilledCost", "category": "direct"}),
    ("/v1/lineage", {"limit": 100}),
    ("/v1/provenance", SCOPE),
    ("/v1/reconciliation", SCOPE),
]


@pytest.fixture
def database(tmp_path: Path) -> Path:
    db = tmp_path / "data-finops.sqlite"
    fill(db)
    return db


def test_concurrent_reads_all_succeed(database: Path) -> None:
    async def load_many_times() -> list[int]:
        transport = httpx.ASGITransport(app=create_app(database))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            requests = [client.get(path, params=params) for path, params in DASHBOARD_LOAD * 8]
            return [response.status_code for response in await asyncio.gather(*requests)]

    statuses = asyncio.run(load_many_times())

    assert statuses == [200] * len(DASHBOARD_LOAD * 8)
