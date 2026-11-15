"""The dashboard's static mode (#228; spec 09 §The site, seam 4): each read the dashboard makes
resolves to a file of a Demo Snapshot, answered as the API answers it, or to the "not in the
public demo" state. The snapshot is the publisher's own (`test_demo_publisher`), and the static
data source runs in Node, as the browser runs it."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_demo_publisher import DEMO, PUBLISHED, TASK_RUN, UNREACHED, _fill

from data_finops.public_demo.publisher import masked, render
from data_finops.reporting import BillingLineDetail, CostSummaries, Reporting

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src" / "data_finops" / "api" / "dashboard"
HARNESS = Path(__file__).with_name("static_harness.js")
PREFIX = "snapshots/20261105T070000Z-0000beef/"


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    folder = tmp_path_factory.mktemp("static")
    database = folder / "data-finops.sqlite"
    _fill(database)
    root = folder / "site"
    for published in render(database, published_at=PUBLISHED, lines=DEMO):
        target = root / PREFIX / published.key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(published.body)
    (root / "current.json").write_text(json.dumps({"prefix": PREFIX}), encoding="utf-8")
    return database, root


def _run(root: Path, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node = shutil.which("node")
    assert node, "the static mode's tests need Node, as the browser runs the same script"
    result = subprocess.run(  # noqa: S603 (a fixed command over files this test wrote)
        [node, str(HARNESS), str(DASHBOARD / "static.js"), str(root)],
        input=json.dumps(calls),
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    answers: list[dict[str, Any]] = json.loads(result.stdout)
    return answers


def _expected(document: Any) -> Any:
    """What the API answers, as the public demo shows it: account IDs masked."""
    return json.loads(masked(document.model_dump_json()))


def _demo_summaries(api: Reporting, basis: str = "BilledCost") -> CostSummaries:
    """The Cost Summary as the publisher writes it: only the scopes with a demo line."""
    answer = api.cost_summary(cost_basis=basis)
    return CostSummaries(
        summaries=[s for s in answer.summaries if s.currencies], uncalculated_scopes=[]
    )


def _scope_and_run(database: Path) -> tuple[dict[str, str], str]:
    with Reporting(database, lines=DEMO) as api:
        (summary,) = _demo_summaries(api).summaries
    scope = {
        "billing_account_id": "123456789012",
        "billing_period": "2026-10-01",
        "source": summary.scope.source,
    }
    return scope, summary.snapshot.calculation_run_id


def test_every_read_the_dashboard_makes_resolves_as_the_api_answers(
    site: tuple[Path, Path],
) -> None:
    database, root = site
    scope, run = _scope_and_run(database)
    with Reporting(database, lines=DEMO) as api:
        allocations = api.allocations(calculation_run_id=run, limit=1000).items
        direct = next(e for e in allocations if e.allocation.category == "direct")
        roots = api.root_runs(limit=100)
        calls_and_answers = (
            [
                (
                    {"path": "/v1/costs/summary", "params": {"cost_basis": basis}},
                    _demo_summaries(api, basis),
                )
                for basis in ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost")
            ]
            + [
                (
                    {"path": "/v1/rollups", "params": {**scope, "limit": 50, "level": level}},
                    api.rollups(level=level, calculation_run_id=run, limit=50),
                )
                for level in ("model_execution", "workload_run")
            ]
            + [
                (
                    {"path": f"/v1/allocations/{direct.ref.replace('#', '%23')}/explanation"},
                    api.explanation(direct.ref, detail=BillingLineDetail.MINIMAL),
                ),
                (
                    {
                        "path": "/v1/rollups/dimensions/target",
                        "params": {**scope, "limit": 1000, "offset": 0},
                    },
                    api.dimension_rollups(name="target", calculation_run_id=run, limit=1000),
                ),
            ]
        )
    answers = _run(root, [call for call, _ in calls_and_answers])

    for (call, expected), answer in zip(calls_and_answers, answers, strict=True):
        assert answer == {"ok": _expected(expected)}, call["path"]
    published_roots = [r.run_id for r in roots.items]
    (lineage,) = _run(root, [{"path": "/v1/lineage", "params": {"limit": 100}}])
    shown = [r["run_id"] for r in lineage["ok"]["items"]]
    assert shown == [r for r in published_roots if r != UNREACHED]
    with Reporting(database, lines=DEMO) as api:
        expected = [api.lineage(run_id) for run_id in shown]
    answers = _run(root, [{"path": "/v1/lineage", "params": {"run_id": r}} for r in shown])
    assert answers == [{"ok": _expected(e)} for e in expected]


def test_each_run_that_cost_reaches_opens_as_the_api_answers(site: tuple[Path, Path]) -> None:
    """A task or model run opens from a Roll-up and from a Cost Explanation (#271)."""
    database, root = site
    _, run = _scope_and_run(database)
    with Reporting(database, lines=DEMO) as api:
        rollups = api.rollups(level="workload_run", calculation_run_id=run, limit=1000).items
        direct = next(
            e
            for e in api.allocations(calculation_run_id=run, limit=1000).items
            if e.allocation.category == "direct"
        )
        explained = api.explanation(direct.ref, detail=BillingLineDetail.MINIMAL)
        evidence = [r.run_id for r in explained.workload_runs if r is not None]
        runs = sorted({entry.key for entry in rollups} | set(evidence))
        expected = [api.lineage(run_id) for run_id in runs]
    assert TASK_RUN in runs  # not a root run
    answers = _run(root, [{"path": "/v1/lineage", "params": {"run_id": r}} for r in runs])
    assert answers == [{"ok": _expected(e)} for e in expected]


def test_allocation_reads_are_filtered_and_paged_as_the_api_does(site: tuple[Path, Path]) -> None:
    database, root = site
    scope, run = _scope_and_run(database)
    with Reporting(database, lines=DEMO) as api:
        unallocated = api.allocations(
            calculation_run_id=run, category="unallocated", cost_basis="BilledCost"
        )
        everything = api.allocations(calculation_run_id=run, limit=1000)
    first, filtered, sliced = _run(
        root,
        [
            {"path": "/v1/allocations", "params": {**scope, "limit": 1}},
            {
                "path": "/v1/allocations",
                "params": {
                    **scope,
                    "category": "unallocated",
                    "cost_basis": "BilledCost",
                    "limit": 1000,
                    "offset": 0,
                },
            },
            {"path": "/v1/allocations", "params": {**scope, "limit": 2, "offset": 1}},
        ],
    )

    assert first["ok"]["total"] == everything.total and len(first["ok"]["items"]) == 1
    assert [e["ref"] for e in filtered["ok"]["items"]] == [e.ref for e in unallocated.items]
    assert filtered["ok"]["total"] == unallocated.total
    assert [e["ref"] for e in sliced["ok"]["items"]] == [e.ref for e in everything.items[1:3]]
    assert (sliced["ok"]["limit"], sliced["ok"]["offset"]) == (2, 1)


def test_what_the_snapshot_does_not_cover_is_not_in_the_public_demo(
    site: tuple[Path, Path],
) -> None:
    database, root = site
    scope, run = _scope_and_run(database)
    with Reporting(database) as api:  # unfiltered: the S3 line's Allocation is not published
        everything = api.allocations(calculation_run_id=run, limit=1000).items
    with Reporting(database, lines=DEMO) as api:
        published = {e.ref for e in api.allocations(calculation_run_id=run, limit=1000).items}
    left_out = next(e.ref for e in everything if e.ref not in published)
    unreached_task = "01a19dd8-4a8c-7abf-9e93-25adbcb5d081"  # the reference DAG run's task
    calls = [
        {"path": "/v1/rollups/dimensions/profile", "params": scope},  # a sensitive dimension
        {"path": f"/v1/allocations/{left_out.replace('#', '%23')}/explanation"},
        {"path": "/v1/lineage", "params": {"run_id": UNREACHED}},  # no published cost reaches it
        {"path": "/v1/lineage", "params": {"run_id": unreached_task}},
        {"path": "/v1/allocations", "params": {**scope, "billing_period": "2026-09-01"}},
        {"path": "/v1/reconciliation", "params": scope},
        # A path out of the snapshot, to the site's own current.json, is never followed.
        {"path": "/v1/lineage", "params": {"run_id": "../../../../../current"}},
    ]
    answers = _run(root, calls)

    for call, answer in zip(calls, answers, strict=True):
        assert set(answer) == {"notInDemo"}, call
        assert answer["notInDemo"].endswith("is not in the public demo")


def test_the_banner_says_what_is_simulated_and_when_it_was_published(
    site: tuple[Path, Path],
) -> None:
    _, root = site
    (answer,) = _run(root, [{"banner": "calc-example"}])

    assert answer["ok"] == (
        "Simulated workload; real Athena charges, reconciled to the bill; data trails by about"
        " a day; amounts before credits. Published 2026-11-05 07:00 UTC"
        " · Calculation Run calc-example."
    )


def test_every_route_the_dashboard_calls_is_one_the_static_source_answers() -> None:
    dashboard = (DASHBOARD / "dashboard.js").read_text(encoding="utf-8")
    source = (DASHBOARD / "static.js").read_text(encoding="utf-8")
    called = set(re.findall(r'"(/v1/[a-z/{}_]+)"', dashboard))
    answered = set(re.findall(r'path === "(/v1/[a-z/_]+)"', source))
    answered |= {"/v1/allocations/{ref}/explanation", "/v1/rollups/dimensions/{name}"}  # by pattern
    assert called and called <= answered


def test_locally_the_dashboard_reads_the_api() -> None:
    page = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    assert '<meta name="data-finops-source" content="api">' in page
    script = (DASHBOARD / "dashboard.js").read_text(encoding="utf-8")
    assert 'content === "static"' in script


def _write(root: Path, key: str, document: Any) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_read_spans_pages_and_needs_one_scope(tmp_path: Path) -> None:
    # A hand-made site: two scopes (October and November), and 1,001 Allocations in October,
    # so that the static source must read a second page, as for a large Demo Snapshot.
    prefix = "snapshots/x/"
    _write(tmp_path, "current.json", {"prefix": prefix})
    scopes = [
        {
            "scope": {
                "billing_account_id": "123456789012",
                "billing_period_start": f"2026-{m}-01T00:00:00Z",
                "source": "s",
            },
            "snapshot": {"calculation_run_id": f"run-{m}"},
        }
        for m in ("10", "11")
    ]
    _write(tmp_path, f"{prefix}v1/costs/summary/BilledCost.json", {"summaries": scopes})
    items = [{"ref": f"run-10#{n}", "allocation": {"category": "direct"}} for n in range(1001)]
    for page in (0, 1):
        _write(
            tmp_path,
            f"{prefix}v1/allocations/run-10/page-{page}.json",
            {
                "calculation_run_id": "run-10",
                "total": 1001,
                "limit": 1000,
                "offset": page * 1000,
                "items": items[page * 1000 : (page + 1) * 1000],
            },
        )
    october = {"billing_period": "2026-10-01"}
    last, either = _run(
        tmp_path,
        [
            {"path": "/v1/allocations", "params": {**october, "limit": 1000, "offset": 1000}},
            {"path": "/v1/allocations", "params": {"limit": 1}},
        ],
    )

    assert [e["ref"] for e in last["ok"]["items"]] == ["run-10#1000"]
    assert last["ok"]["total"] == 1001
    assert either == {"notInDemo": "That Snapshot Scope is not in the public demo"}
