"""The publisher of the Demo Snapshot (#227; spec 09 §The publisher; ADR 0029): the read model's
answers for the public demo, as JSON files in the shapes the HTTP API returns.

Only the demo workgroup's Athena usage lines are read (`demo_lines`, Q82), so credits and the
rest of the bill are left out, and so is everything that comes from them. The Cost
Explanations are minimal (ADR 0028), and sensitive Cost Dimensions are left out everywhere
(ADR 0031). Every 12-digit account ID becomes the example account, `123456789012`.

The files go under a new prefix, `snapshots/<published at>/`. Only then does `current.json`
change to name it, so a reader never sees half a snapshot. The bucket's lifecycle expires old
prefixes. The layout, which the dashboard's static mode reads (#228), is in `KEYS`.
"""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusChargeCategory
from data_finops.providers.aws.calls import call
from data_finops.public_demo.workload import WORKGROUP
from data_finops.reporting import (
    MAX_PAGE_SIZE,
    BillingLineDetail,
    CostSummaries,
    Reporting,
    RollupLevel,
    without_sensitive_dimensions,
)

EXAMPLE_ACCOUNT = "123456789012"
ACCOUNT_ID = re.compile(r"(?<![\d.])(?<![0-9a-fA-F]{4}-)\d{12}(?![\d.])")
"""A 12-digit number that is not part of a longer number, a decimal or the last group of a
UUID (`8-4-4-4-12` hex digits)."""
SNAPSHOTS = "snapshots/"
CURRENT = "current.json"
KEYS = {
    "summary": "v1/costs/summary/{cost_basis}.json",
    "allocations": "v1/allocations/{run}/page-{page}.json",
    "explanation": "v1/allocations/{run}/{position}/explanation.json",
    "rollups": "v1/rollups/{level}/{run}/page-{page}.json",
    "dimension": "v1/rollups/dimensions/{name}/{run}/page-{page}.json",
    "roots": "v1/lineage/page-{page}.json",
    "lineage": "v1/lineage/runs/{run_id}.json",
    "snapshot": "snapshot.json",
}
"""Each file of a Demo Snapshot, under its prefix. A page holds `MAX_PAGE_SIZE` items at most;
page 0 is always written. `{run}` is the Calculation Run of a Billing Period's snapshot,
which the summary names."""

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PublishedFile:
    key: str
    """The file's path under the snapshot's prefix (`KEYS`)."""
    body: bytes


def demo_lines(workgroup: str = WORKGROUP) -> Callable[[FocusBillingLine], bool]:
    """The Billing Lines of the demo: Athena usage whose resource is the demo workgroup."""
    suffix = f":workgroup/{workgroup}"

    def keeps(line: FocusBillingLine) -> bool:
        return (
            line.ChargeCategory is FocusChargeCategory.USAGE
            and line.ResourceId is not None
            and line.ResourceId.endswith(suffix)
        )

    return keeps


def billing_periods(moment: datetime) -> tuple[date, date]:
    """The previous and the current Billing Period of `moment`, by their first day."""
    current = date(moment.year, moment.month, 1)
    previous = (
        date(current.year - 1, 12, 1)
        if current.month == 1
        else current.replace(month=current.month - 1)
    )
    return previous, current


def render(
    database: Path, *, published_at: datetime, lines: Callable[[FocusBillingLine], bool]
) -> list[PublishedFile]:
    """Every file of the Demo Snapshot of the database, its account IDs already masked."""
    files: list[PublishedFile] = []
    with Reporting(database, lines=lines) as read_model:
        summaries = {
            basis: _summaries(read_model, basis, billing_periods(published_at))
            for basis in CostBasis
        }
        for basis, summary in summaries.items():
            files.append(_file(KEYS["summary"].format(cost_basis=basis.value), summary))
        runs = [s.snapshot.calculation_run_id for s in summaries[CostBasis.BILLED_COST].summaries]
        reached: set[str] = set()
        for run in runs:
            files += _allocations(read_model, run)
            for level in get_args(RollupLevel):
                pages = list(_rollups(read_model, run, level))
                files += [
                    _file(KEYS["rollups"].format(level=level, run=run, page=n), p) for n, p in pages
                ]
                if level == "workload_run":
                    reached |= {entry.key for _, page in pages for entry in page.items}
            for name in _public_dimensions(read_model, run):
                for n, page in _dimension_pages(read_model, run, name):
                    files.append(_file(KEYS["dimension"].format(name=name, run=run, page=n), page))
        files += _lineage(read_model, reached)
        files.append(_snapshot_file(published_at, summaries[CostBasis.BILLED_COST]))
    return files


def publish(
    database: Path,
    *,
    s3: Any,
    bucket: str,
    clock: Clock,
    lines: Callable[[FocusBillingLine], bool] | None = None,
) -> dict[str, Any]:
    """Render the Demo Snapshot, write it under a new prefix, then point `current.json` at it.
    A failed write raises AwsSourceError, and `current.json` keeps the last whole snapshot."""
    published_at = clock().astimezone(UTC).replace(microsecond=0)
    files = render(database, published_at=published_at, lines=lines or demo_lines())
    prefix = f"{SNAPSHOTS}{published_at.strftime('%Y%m%dT%H%M%SZ')}/"
    for file in files:
        _put(s3, bucket, f"{prefix}{file.key}", file.body)
    pointer = {"prefix": prefix, "published_at": _time(published_at), "files": len(files)}
    _put(s3, bucket, CURRENT, json.dumps(pointer).encode("utf-8"))
    return pointer


def _put(s3: Any, bucket: str, key: str, body: bytes) -> None:
    call(s3.put_object, Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def _time(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _masked(text: str) -> str:
    return ACCOUNT_ID.sub(EXAMPLE_ACCOUNT, text)


def _file(key: str, document: BaseModel | dict[str, Any]) -> PublishedFile:
    text = document.model_dump_json() if isinstance(document, BaseModel) else json.dumps(document)
    return PublishedFile(key=key, body=_masked(text).encode("utf-8"))


def _summaries(
    read_model: Reporting, basis: CostBasis, periods: tuple[date, date]
) -> CostSummaries:
    """The Cost Summaries of the published Billing Periods, one read each, as one answer."""
    answers = [read_model.cost_summary(cost_basis=basis, billing_period=p) for p in periods]
    return CostSummaries(
        summaries=[s for answer in answers for s in answer.summaries],
        uncalculated_scopes=[s for answer in answers for s in answer.uncalculated_scopes],
    )


def _pages(total: int) -> range:
    return range(max(1, -(-total // MAX_PAGE_SIZE)))


def _allocations(read_model: Reporting, run: str) -> list[PublishedFile]:
    """The Allocation pages of a run, without sensitive Cost Dimensions, and the minimal Cost
    Explanation of each Allocation."""
    files: list[PublishedFile] = []
    first = read_model.allocations(calculation_run_id=run, limit=MAX_PAGE_SIZE)
    for n in _pages(first.total):
        page = (
            first
            if n == 0
            else read_model.allocations(
                calculation_run_id=run, limit=MAX_PAGE_SIZE, offset=n * MAX_PAGE_SIZE
            )
        )
        items = [
            item.model_copy(update={"allocation": without_sensitive_dimensions(item.allocation)})
            for item in page.items
        ]
        files.append(
            _file(
                KEYS["allocations"].format(run=run, page=n),
                page.model_copy(update={"items": items}),
            )
        )
        for item in page.items:
            position = item.ref.rpartition("#")[2]
            explanation = read_model.explanation(item.ref, detail=BillingLineDetail.MINIMAL)
            files.append(_file(KEYS["explanation"].format(run=run, position=position), explanation))
    return files


def _rollups(read_model: Reporting, run: str, level: RollupLevel) -> Iterator[tuple[int, Any]]:
    first = read_model.rollups(level=level, calculation_run_id=run, limit=MAX_PAGE_SIZE)
    for n in _pages(first.total):
        yield (
            n,
            first
            if n == 0
            else read_model.rollups(
                level=level, calculation_run_id=run, limit=MAX_PAGE_SIZE, offset=n * MAX_PAGE_SIZE
            ),
        )


def _public_dimensions(read_model: Reporting, run: str) -> list[str]:
    """The Cost Dimensions of the run's Allocations that are never sensitive."""
    names: dict[str, bool] = {}
    first = read_model.allocations(calculation_run_id=run, limit=MAX_PAGE_SIZE)
    for n in _pages(first.total):
        page = (
            first
            if n == 0
            else read_model.allocations(
                calculation_run_id=run, limit=MAX_PAGE_SIZE, offset=n * MAX_PAGE_SIZE
            )
        )
        for item in page.items:
            for name, value in item.allocation.dimensions.items():
                names[name] = names.get(name, False) or value.sensitive
    return sorted(name for name, sensitive in names.items() if not sensitive)


def _dimension_pages(read_model: Reporting, run: str, name: str) -> Iterator[tuple[int, Any]]:
    first = read_model.dimension_rollups(name=name, calculation_run_id=run, limit=MAX_PAGE_SIZE)
    for n in _pages(first.total):
        yield (
            n,
            first
            if n == 0
            else read_model.dimension_rollups(
                name=name, calculation_run_id=run, limit=MAX_PAGE_SIZE, offset=n * MAX_PAGE_SIZE
            ),
        )


def _lineage(read_model: Reporting, reached: set[str]) -> list[PublishedFile]:
    """The topmost Workload Runs that published cost reaches, and the lineage of each."""
    roots = []
    offset = 0
    while True:
        page = read_model.root_runs(limit=MAX_PAGE_SIZE, offset=offset)
        roots += [run for run in page.items if run.run_id in reached]
        offset += MAX_PAGE_SIZE
        if offset >= page.total:
            break
    files = []
    for n in _pages(len(roots)):
        items = roots[n * MAX_PAGE_SIZE : (n + 1) * MAX_PAGE_SIZE]
        shown = page.model_copy(
            update={
                "total": len(roots),
                "limit": MAX_PAGE_SIZE,
                "offset": n * MAX_PAGE_SIZE,
                "items": items,
            }
        )
        files.append(_file(KEYS["roots"].format(page=n), shown))
    for run in roots:
        files.append(
            _file(KEYS["lineage"].format(run_id=run.run_id), read_model.lineage(run.run_id))
        )
    return files


def _snapshot_file(published_at: datetime, billed: CostSummaries) -> PublishedFile:
    """When the snapshot was published, and for each Billing Period its Calculation Run and the
    share of its billed cost that is explained (not Unallocated), before credits."""
    periods = []
    for summary in billed.summaries:
        for currency in summary.currencies:
            amount = Decimal(currency.billed)
            explained = sum((Decimal(a.amount) for a in currency.allocated), Decimal(0))
            periods.append(
                {
                    "billing_period": summary.scope.billing_period_start.date().isoformat(),
                    "calculation_run_id": summary.snapshot.calculation_run_id,
                    "currency": currency.currency,
                    "billed": str(amount),
                    "explained_share": str(round(explained / amount, 4)) if amount else None,
                }
            )
    document = {
        "published_at": _time(published_at),
        "cost_basis": CostBasis.BILLED_COST.value,
        "before_credits": True,
        "periods": periods,
    }
    return _file(KEYS["snapshot"], document)
