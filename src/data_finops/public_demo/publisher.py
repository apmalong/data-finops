"""The publisher of the Demo Snapshot (#227; spec 09 §The publisher; ADR 0029): the read model's
answers for the public demo, as JSON files in the shapes the HTTP API returns.

Only the demo workgroup's Athena usage lines are read (`demo_lines`, Q82), so credits and the
rest of the bill are left out, and so is everything that comes from them. A Snapshot Scope with
none of those lines is not published at all. The Cost Explanations are minimal (ADR 0028), and
sensitive Cost Dimensions are left out everywhere (ADR 0031). Every 12-digit account ID becomes
the example account, `123456789012`.

The files go under a new prefix, `snapshots/<published at>-<random>/`. Only then does
`current.json` change to name it, so a reader never sees half a snapshot. The bucket's lifecycle
expires old prefixes. The layout, which the dashboard's static mode reads (#228), is in `KEYS`.
"""

import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusChargeCategory
from data_finops.providers.aws import WriteConflict, put_bytes, read_versioned_object
from data_finops.public_demo.workload import WORKGROUP
from data_finops.reporting import (
    MAX_PAGE_SIZE,
    AllocationPage,
    BillingLineDetail,
    CostSummaries,
    LineFilter,
    NotFound,
    Reporting,
    RollupLevel,
    without_sensitive_dimensions,
)

EXAMPLE_ACCOUNT = "123456789012"
ACCOUNT_ID = re.compile(r"(?<![0-9a-fA-F.])(?<![0-9a-fA-F]{4}-)\d{12}(?![0-9a-fA-F])")
"""A run of 12 digits that is an account ID: not part of a longer number, of hex text (a
SHA-256) or of a decimal's fraction, and not the last group of a UUID (`8-4-4-4-12`)."""
SNAPSHOTS = "snapshots/"
CURRENT = "current.json"
MAX_POINTER_BYTES = 4096
MAX_MOVES = 5
"""How many times the publisher tries to move `current.json` while other publishers move it."""
JSON = "application/json"
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
page 0 is always written. `{run}` is the Calculation Run of a Billing Period's snapshot, which
the summary names with its scope."""

Clock = Callable[[], datetime]


class PointerContended(RuntimeError):
    """`current.json` kept changing while the publisher tried to move it."""


@dataclass(frozen=True)
class PublishedFile:
    key: str
    """The file's path under the snapshot's prefix (`KEYS`)."""
    body: bytes
    """The JSON, with every account ID masked."""


class PublishedPeriod(BaseModel):
    """One Billing Period of a Demo Snapshot, at the BilledCost basis, before credits."""

    billing_period: date
    calculation_run_id: str
    currency: str
    billed: Decimal
    explained_share: Decimal | None
    """The share of `billed` that is not Unallocated, to 4 places; None when nothing is
    billed."""


class DemoSnapshot(BaseModel):
    """`snapshot.json`: when the Demo Snapshot was published, and what it covers."""

    published_at: datetime
    cost_basis: CostBasis = CostBasis.BILLED_COST
    before_credits: bool = True
    periods: list[PublishedPeriod]


def demo_lines(workgroup: str = WORKGROUP) -> LineFilter:
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
    if current.month == 1:
        return date(current.year - 1, 12, 1), current
    return current.replace(month=current.month - 1), current


def masked(text: str) -> str:
    """The text with each account ID made the example account."""
    return ACCOUNT_ID.sub(EXAMPLE_ACCOUNT, text)


def render(database: Path, *, published_at: datetime, lines: LineFilter) -> list[PublishedFile]:
    """Every file of the Demo Snapshot of the database, its account IDs already masked."""
    files: list[PublishedFile] = []
    with Reporting(database, lines=lines) as read_model:
        periods = billing_periods(published_at)
        summaries = {basis: _summaries(read_model, basis, periods) for basis in CostBasis}
        for basis, summary in summaries.items():
            files.append(_file(KEYS["summary"].format(cost_basis=basis.value), summary))
        reached: set[str] = set()
        for scope in summaries[CostBasis.BILLED_COST].summaries:
            run = scope.snapshot.calculation_run_id
            selected: dict[str, Any] = {"calculation_run_id": run, "limit": MAX_PAGE_SIZE}
            allocations = [page for _, page in _pages(partial(read_model.allocations, **selected))]
            files += _allocation_files(read_model, run, allocations)
            for level in get_args(RollupLevel):
                for n, page in _pages(partial(read_model.rollups, level=level, **selected)):
                    files.append(_file(KEYS["rollups"].format(level=level, run=run, page=n), page))
                    if level == "workload_run":
                        reached |= {entry.key for entry in page.items}
            for name in _public_dimensions(allocations):
                pages = _pages(partial(read_model.dimension_rollups, name=name, **selected))
                for n, page in pages:
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
    lines: LineFilter | None = None,
) -> dict[str, Any]:
    """Render the Demo Snapshot, write it under a new prefix, then point `current.json` at it.
    A failed write raises AwsSourceError, and `current.json` keeps the last whole snapshot.
    The prefix has a random part, so two publishes never share one, and `current.json` moves
    only forward in time (`moved` says whether this publish moved it)."""
    published_at = clock().astimezone(UTC).replace(microsecond=0)
    files = render(database, published_at=published_at, lines=lines or demo_lines())
    stamp = published_at.strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{SNAPSHOTS}{stamp}-{uuid.uuid4().hex[:8]}/"
    for file in files:
        put_bytes(s3, bucket, f"{prefix}{file.key}", file.body, content_type=JSON)
    pointer = {"prefix": prefix, "published_at": _time(published_at), "files": len(files)}
    return pointer | {"moved": _move_current(s3, bucket, pointer)}


def _move_current(s3: Any, bucket: str, pointer: dict[str, Any]) -> bool:
    """Point `current.json` at the new snapshot, unless it already names a snapshot published
    at the same time or later. The write is conditional on the version that was read, so two
    publishers at once can never move it back (spec 09 §The publisher)."""
    body = json.dumps(pointer).encode("utf-8")
    for _ in range(MAX_MOVES):
        current, etag = _read_current(s3, bucket)
        if current is not None and str(current.get("published_at", "")) >= pointer["published_at"]:
            return False
        try:
            put_bytes(s3, bucket, CURRENT, body, content_type=JSON, etag=etag)
            return True
        except WriteConflict:
            continue  # another publisher moved it: read it again
    raise PointerContended(f"{CURRENT} changed {MAX_MOVES} times while it was being moved")


def _read_current(s3: Any, bucket: str) -> tuple[dict[str, Any] | None, str | None]:
    """`current.json` and its ETag, or (None, None) when there is none yet."""
    found = read_versioned_object(s3, bucket, CURRENT, MAX_POINTER_BYTES)
    if found is None:
        return None, None
    data, etag = found
    document: dict[str, Any] = json.loads(data)
    return document, etag


def _time(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _file(key: str, document: BaseModel) -> PublishedFile:
    return PublishedFile(key=key, body=masked(document.model_dump_json()).encode("utf-8"))


def _summaries(
    read_model: Reporting, basis: CostBasis, periods: tuple[date, date]
) -> CostSummaries:
    """The Cost Summaries of the published Billing Periods, as one answer. A scope with no
    demo line is left out, and so is every scope not yet calculated: neither is the demo's."""
    answers = [read_model.cost_summary(cost_basis=basis, billing_period=p) for p in periods]
    return CostSummaries(
        summaries=[s for answer in answers for s in answer.summaries if s.currencies],
        uncalculated_scopes=[],
    )


def _pages(fetch: Callable[..., Any]) -> Iterator[tuple[int, Any]]:
    """Each page of a paged read, by number; page 0 even when it is empty. `fetch` takes the
    page's `offset`."""
    page, n = fetch(offset=0), 0
    yield n, page
    while (n + 1) * MAX_PAGE_SIZE < page.total:
        n += 1
        yield n, fetch(offset=n * MAX_PAGE_SIZE)


def _allocation_files(
    read_model: Reporting, run: str, pages: list[AllocationPage]
) -> list[PublishedFile]:
    """The Allocation pages of a run, without sensitive Cost Dimensions, and the minimal Cost
    Explanation of each Allocation."""
    files: list[PublishedFile] = []
    for n, page in enumerate(pages):
        items = [
            item.model_copy(update={"allocation": without_sensitive_dimensions(item.allocation)})
            for item in page.items
        ]
        shown = page.model_copy(update={"items": items})
        files.append(_file(KEYS["allocations"].format(run=run, page=n), shown))
        for item in page.items:
            position = item.ref.rpartition("#")[2]
            explanation = read_model.explanation(item.ref, detail=BillingLineDetail.MINIMAL)
            files.append(_file(KEYS["explanation"].format(run=run, position=position), explanation))
    return files


def _public_dimensions(pages: list[AllocationPage]) -> list[str]:
    """The Cost Dimensions of the Allocations that are never sensitive."""
    sensitive: dict[str, bool] = {}
    for page in pages:
        for item in page.items:
            for name, value in item.allocation.dimensions.items():
                sensitive[name] = sensitive.get(name, False) or value.sensitive
    return sorted(name for name, is_sensitive in sensitive.items() if not is_sensitive)


def _lineage(read_model: Reporting, reached: set[str]) -> list[PublishedFile]:
    """The lineage of each Workload Run that published cost reaches, at any depth, so that a
    task run or a model run opens from a Roll-up or a Cost Explanation; then the root runs
    among them. A run known only as the root of a different run has no lineage: it is in
    neither, and the dashboard says that it is not in the public demo."""
    files, published = [], set()
    for run_id in sorted(reached):
        try:
            lineage = read_model.lineage(run_id)
        except NotFound:
            continue
        files.append(_file(KEYS["lineage"].format(run_id=run_id), lineage))
        published.add(run_id)
    return _root_pages(read_model, published) + files


def _root_pages(read_model: Reporting, published: set[str]) -> list[PublishedFile]:
    """The pages of the root runs whose lineage is published."""
    pages = list(_pages(partial(read_model.root_runs, limit=MAX_PAGE_SIZE)))
    roots = [run for _, page in pages for run in page.items if run.run_id in published]
    template = pages[0][1]
    files = []
    for n in range(max(1, -(-len(roots) // MAX_PAGE_SIZE))):
        shown = template.model_copy(
            update={
                "total": len(roots),
                "limit": MAX_PAGE_SIZE,
                "offset": n * MAX_PAGE_SIZE,
                "items": roots[n * MAX_PAGE_SIZE : (n + 1) * MAX_PAGE_SIZE],
            }
        )
        files.append(_file(KEYS["roots"].format(page=n), shown))
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
                PublishedPeriod(
                    billing_period=summary.scope.billing_period_start.date(),
                    calculation_run_id=summary.snapshot.calculation_run_id,
                    currency=currency.currency,
                    billed=amount,
                    explained_share=round(explained / amount, 4) if amount else None,
                )
            )
    return _file(KEYS["snapshot"], DemoSnapshot(published_at=published_at, periods=periods))
