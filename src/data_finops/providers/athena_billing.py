"""Athena on-demand billing policy (spec 03, M4 structure 8; ADR 0017).

Provider policy, never core logic, in two independently versioned parts: how much of a
Query Execution Athena bills (`POLICY_VERSION`), and which Billing Line it is billed in
(billing scope, ADR 0023; `SCOPE_POLICY_VERSION`). The engine in `attribution` takes
both results as given; a Calculation Run records both versions (#88).

Documented by AWS: data scanned is rounded up to the nearest megabyte with a 10 MB
minimum per query; failed queries and DDL are not charged; cancelled queries are charged
for the data scanned. Checked against the real 2026-11 FOCUS export (#91; OQ-3, OQ-24):
- ConsumedUnit reads `TB` (`Terabytes`, the unit's name in AWS's pricing pages, is
  accepted too). `/1` and `/2` read it as 10^12 bytes and MB as 10^6. The minimum-only
  lines of #91 fit that, but cannot tell it from binary units. `/3` reads `TB` as 2^40
  bytes, fitted to the owner's demo lines (#310; `docs/research/m7-athena-billing-units.md`).
  No AWS source states either;
- `DataScannedInBytes` is the raw scan, before the minimum and rounding;
- a query that scanned 0 bytes bills nothing, whatever its statement type: SELECTs, an
  INSERT, a VACUUM and a reused result billed nothing (that a reused result always reports
  0 bytes is AWS's documentation, observed only for a 0-byte original);
- CTAS (labelled `DDL`), INSERT INTO and OPTIMIZE (labelled `DDL`) that scanned any bytes
  billed the 10 MB minimum each;
- a line names its workgroup by ARN in ResourceId, and one line may span several hours of
  usage (00:00 to 05:00 for five hourly probes), so scope is the line's charge period.

Each rule is versioned; an earlier version stays callable as it was, so what a Calculation
Run recorded under it can be recomputed. A Calculation Run itself always uses the current
versions. `in_billing_scope` did not change between the scope versions. `/1` charged the
minimum for any 0-byte query but DDL, which the export contradicts (OQ-24);
`athena-billing-scope/1` keyed the pool label by day.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, localcontext

from data_finops.contracts.billing import FocusBillingLine
from data_finops.contracts.workload import QueryExecution

BILLABLE_USAGE_V1 = "athena-billable-usage/1"
BILLABLE_USAGE_V2 = "athena-billable-usage/2"
BILLABLE_USAGE_V3 = "athena-billable-usage/3"
POLICY_VERSION = BILLABLE_USAGE_V3  # the current Billable Usage version
BILLING_SCOPE_V1 = "athena-billing-scope/1"
BILLING_SCOPE_V2 = "athena-billing-scope/2"
SCOPE_POLICY_VERSION = BILLING_SCOPE_V2  # the current billing scope version

_MB = 1_000_000
_MINIMUM = 10 * _MB
_BILLED_STATES = frozenset({"SUCCEEDED", "CANCELLED"})
_UNBILLED_STATES = frozenset({"QUEUED", "RUNNING", "FAILED"})
_BYTES_PER_UNIT = {"TB": 10**12, "Terabytes": 10**12}
_BINARY_BYTES_PER_UNIT = {"TB": 2**40, "Terabytes": 2**40}
_V3_MINIMUM = Decimal("0.00001")  # of the billed unit, for each query
_V3_STEP = Decimal("0.000005")  # each hour is rounded down to a multiple of this


def billable_bytes(query: QueryExecution, version: str = BILLABLE_USAGE_V2) -> int | None:
    """The bytes Athena bills for `query` under `version`, or None when a billed query
    reported no scan.

    Succeeded and cancelled queries bill their scan rounded up to a megabyte, at least
    10 MB, except a query that scanned nothing: under `/2` it bills nothing, under `/1`
    only a DDL statement did (Athena labels CTAS `DDL` too). Failed queries bill nothing,
    and so do queued or running ones in this observation (a later observation of the
    finished query is stored separately). Raises ValueError for a state Athena does not
    document or a version this module does not know. `/3` has no rule for one query alone:
    use `billable_quantities`.
    """
    if version not in (BILLABLE_USAGE_V1, BILLABLE_USAGE_V2):
        raise ValueError(f"unknown Athena Billable Usage policy {version!r}")
    if query.state in _UNBILLED_STATES:
        return 0
    if query.state not in _BILLED_STATES:
        raise ValueError(f"{query.query_execution_id}: unknown Athena state {query.state!r}")
    if query.scanned_bytes is None:
        return None
    if query.scanned_bytes == 0 and (version == BILLABLE_USAGE_V2 or query.statement_type == "DDL"):
        return 0
    return max(-(-query.scanned_bytes // _MB) * _MB, _MINIMUM)


def billable_quantities(
    queries: Sequence[QueryExecution], consumed_unit: str, version: str = POLICY_VERSION
) -> list[Decimal | None]:
    """The Billable Usage of each of a line's `queries`, in its `consumed_unit`, in order:
    None where it is unknown (a billed query that reported no scan).

    `/3` (#310): a billed query is its raw scan in units of 2^40 bytes, at least 10^-5 of
    the unit, and a query that scanned nothing is 0. The queries of each UTC hour of
    completion are summed. That sum is rounded down to a multiple of 5 x 10^-6. Each query
    gets a part of it, in proportion to its quantity after the minimum. The hour's last query
    gets the remainder, so the hour sums exactly. The hours are those of the queries given:
    a line whose period does not start and end on the hour would round a split hour in each
    line (real lines are hour-aligned, #91). `/1` and `/2` convert
    `billable_bytes` of each query alone. Raises ValueError for an unknown unit or version.
    """
    if version in (BILLABLE_USAGE_V1, BILLABLE_USAGE_V2):
        return [
            None
            if (scanned := billable_bytes(q, version)) is None
            else bytes_in_consumed_unit(scanned, consumed_unit)
            for q in queries
        ]
    if version != BILLABLE_USAGE_V3:
        raise ValueError(f"unknown Athena Billable Usage policy {version!r}")
    if consumed_unit not in _BINARY_BYTES_PER_UNIT:
        raise ValueError(f"unknown Athena ConsumedUnit {consumed_unit!r}")
    unit = _BINARY_BYTES_PER_UNIT[consumed_unit]
    raw: list[Decimal | None] = []
    hours: dict[datetime, Decimal] = defaultdict(Decimal)
    for query in queries:
        quantity = _raw_quantity(query, unit)
        raw.append(quantity)
        if quantity:
            hours[_hour(query)] += quantity
    billed = {
        hour: (total / _V3_STEP).to_integral_value(ROUND_FLOOR) * _V3_STEP
        for hour, total in hours.items()
    }
    last = {
        _hour(q): i for i, (q, quantity) in enumerate(zip(queries, raw, strict=True)) if quantity
    }
    shares: list[Decimal | None] = []
    given: dict[datetime, Decimal] = defaultdict(Decimal)
    with localcontext() as context:
        context.prec = 28
        for i, (query, quantity) in enumerate(zip(queries, raw, strict=True)):
            if not quantity:
                shares.append(quantity)
                continue
            hour = _hour(query)
            # The hour's last query takes what is left, so each hour sums to its bill exactly.
            share = (
                billed[hour] - given[hour]
                if last[hour] == i
                else quantity * billed[hour] / hours[hour]
            )
            given[hour] += share
            shares.append(share)
    return shares


def _raw_quantity(query: QueryExecution, unit: int) -> Decimal | None:
    """Under `/3`: what one query adds to its hour before the rounding, or None if unknown."""
    if query.state in _UNBILLED_STATES:
        return Decimal(0)
    if query.state not in _BILLED_STATES:
        raise ValueError(f"{query.query_execution_id}: unknown Athena state {query.state!r}")
    if query.scanned_bytes is None:
        return None
    if query.scanned_bytes == 0:
        return Decimal(0)
    with localcontext() as context:
        context.prec = 28
        return max(Decimal(query.scanned_bytes) / unit, _V3_MINIMUM)


def _hour(query: QueryExecution) -> datetime:
    """The UTC hour a query completed in (submitted, if it has no completion time)."""
    moment = (query.completed_at or query.submitted_at).astimezone(UTC)
    return moment.replace(minute=0, second=0, microsecond=0)


def bytes_in_consumed_unit(billable: int, consumed_unit: str) -> Decimal:
    """`billable` bytes in a Billing Line's ConsumedUnit, so Coverage compares like with like."""
    if consumed_unit not in _BYTES_PER_UNIT:
        raise ValueError(f"unknown Athena ConsumedUnit {consumed_unit!r}")
    return Decimal(billable) / _BYTES_PER_UNIT[consumed_unit]


def is_on_demand_usage(line: FocusBillingLine) -> bool:
    """Whether a usage line bills Athena on-demand scanning, so it can be metered
    (`POLICY_VERSION`; ADR 0017): Athena's service, with a positive ConsumedQuantity in a
    unit `bytes_in_consumed_unit` knows: the real export writes `Amazon Athena` and `TB`
    (#91). Which lines are usage at all is the Calculation Run's (step B).
    """
    return (
        line.ServiceName == "Amazon Athena"
        and line.ConsumedUnit in _BYTES_PER_UNIT
        and line.ConsumedQuantity is not None
        and line.ConsumedQuantity > 0
    )


def pool_id(line: FocusBillingLine, version: str = SCOPE_POLICY_VERSION) -> str:
    """The metered Cost Pool a line belongs to, by its billing scope keys: usage account,
    region, workgroup (`*` when the line names none) and, under `/2`, the line's charge
    period in UTC (`/1` used its start day, though one day can hold several lines)."""
    if version not in (BILLING_SCOPE_V1, BILLING_SCOPE_V2):
        raise ValueError(f"unknown Athena billing scope policy {version!r}")
    workgroup = _workgroup(line.ResourceId) or "*"
    if version == BILLING_SCOPE_V2:
        period = f"{_utc(line.ChargePeriodStart)}/{_utc(line.ChargePeriodEnd)}"
    else:
        period = line.ChargePeriodStart.date().isoformat()
    return f"athena-on-demand:{line.SubAccountId}:{line.RegionId}:{workgroup}:{period}"


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def in_billing_scope(query: QueryExecution, line: FocusBillingLine) -> bool:
    """Whether `query` falls in the scope Athena billed `line` for (ADR 0023).

    Accounting scope, never Evidence: the query ran in the line's usage account (FOCUS
    SubAccountId, not the paying BillingAccountId) and region, in the line's workgroup if
    the line names one, and completed within the line's charge period, start inclusive,
    however many hours that period spans. A query that has not completed is not yet
    billed. Link Outcomes are not an input: linked and unlinked queries are scoped alike.
    A line names its workgroup by a workgroup ARN in ResourceId (#91); a line naming none
    does not restrict by workgroup. Completion time is the timestamp scope uses, though it
    is not always the one Athena bills by: two queries that completed seconds before an hour
    were billed in the next hour's line (OQ-25), which then keeps them out of its scope.
    """
    workgroup = _workgroup(line.ResourceId)
    return (
        query.account_id == line.SubAccountId
        and query.region == line.RegionId
        and (workgroup is None or query.workgroup == workgroup)
        and query.completed_at is not None
        and line.ChargePeriodStart <= query.completed_at < line.ChargePeriodEnd
    )


def line_workgroup(line: FocusBillingLine) -> str | None:
    """The Athena workgroup a Billing Line names in ResourceId, or None (#91). The
    `workgroup_map` Cost Dimension source uses it for an amount with no query (ADR 0031)."""
    return _workgroup(line.ResourceId)


def _workgroup(resource_id: str | None) -> str | None:
    """The workgroup named by an Athena workgroup ARN, e.g. `...:workgroup/demo`."""
    fields = (resource_id or "").split(":", 5)
    if len(fields) < 6 or fields[0] != "arn" or fields[2] != "athena":
        return None
    kind, _, name = fields[5].partition("/")
    return name if kind == "workgroup" and name else None
