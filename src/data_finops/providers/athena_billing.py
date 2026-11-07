"""Athena on-demand billing policy (spec 03, M4 structure 8; ADR 0017).

Provider policy, never core logic, in two independently versioned parts: how much of a
Query Execution Athena bills (`POLICY_VERSION`), and which Billing Line it is billed in
(billing scope, ADR 0023; `SCOPE_POLICY_VERSION`). The engine in `attribution` takes
both results as given; a Calculation Run records both versions (#88).

Documented by AWS: data scanned is rounded up to the nearest megabyte with a 10 MB
minimum per query; failed queries and DDL are not charged; cancelled queries are charged
for the data scanned. Checked against the real 2026-11 FOCUS export (#91; OQ-3, OQ-24):
- units are decimal (MB = 10^6 bytes, TB = 10^12 bytes), and ConsumedUnit reads `TB`;
- `DataScannedInBytes` is the raw scan, before the minimum and rounding;
- a query that scanned 0 bytes bills nothing, whatever its statement type: SELECTs, an
  INSERT, a VACUUM and a reused result (which reports 0 bytes) billed nothing;
- CTAS (labelled `DDL`), INSERT INTO and OPTIMIZE (labelled `DDL`) that scanned any bytes
  billed the 10 MB minimum each;
- a line names its workgroup by ARN in ResourceId, and one line may span several hours of
  usage (00:00 to 05:00 for five hourly probes), so scope is the line's charge period.

Each rule is versioned; an earlier version stays as it was, so a Calculation Run recorded
under it can be reproduced. `/1` charged the minimum for any 0-byte query but DDL, which
the export contradicts (OQ-24); `athena-billing-scope/1` keyed the pool label by day.
"""

from datetime import UTC, datetime
from decimal import Decimal

from data_finops.contracts.billing import FocusBillingLine
from data_finops.contracts.workload import QueryExecution

BILLABLE_USAGE_V1 = "athena-billable-usage/1"
POLICY_VERSION = "athena-billable-usage/2"
BILLING_SCOPE_V1 = "athena-billing-scope/1"
SCOPE_POLICY_VERSION = "athena-billing-scope/2"

_MB = 1_000_000
_MINIMUM = 10 * _MB
_BILLED_STATES = frozenset({"SUCCEEDED", "CANCELLED"})
_UNBILLED_STATES = frozenset({"QUEUED", "RUNNING", "FAILED"})
_BYTES_PER_UNIT = {"TB": 10**12, "Terabytes": 10**12}


def billable_bytes(query: QueryExecution, version: str = POLICY_VERSION) -> int | None:
    """The bytes Athena bills for `query` under `version`, or None when a billed query
    reported no scan.

    Succeeded and cancelled queries bill their scan rounded up to a megabyte, at least
    10 MB, except a query that scanned nothing: under `/2` it bills nothing, under `/1`
    only a DDL statement did (Athena labels CTAS `DDL` too). Failed queries bill nothing,
    and so do queued or running ones in this observation (a later observation of the
    finished query is stored separately). Raises ValueError for a state Athena does not
    document or a version this module does not know.
    """
    if version not in (BILLABLE_USAGE_V1, POLICY_VERSION):
        raise ValueError(f"unknown Athena Billable Usage policy {version!r}")
    if query.state in _UNBILLED_STATES:
        return 0
    if query.state not in _BILLED_STATES:
        raise ValueError(f"{query.query_execution_id}: unknown Athena state {query.state!r}")
    if query.scanned_bytes is None:
        return None
    if query.scanned_bytes == 0 and (version == POLICY_VERSION or query.statement_type == "DDL"):
        return 0
    return max(-(-query.scanned_bytes // _MB) * _MB, _MINIMUM)


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
    workgroup = _workgroup(line.ResourceId) or "*"
    if version == BILLING_SCOPE_V1:
        period = line.ChargePeriodStart.date().isoformat()
    elif version == SCOPE_POLICY_VERSION:
        period = f"{_utc(line.ChargePeriodStart)}/{_utc(line.ChargePeriodEnd)}"
    else:
        raise ValueError(f"unknown Athena billing scope policy {version!r}")
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
    does not restrict by workgroup. Completion time is the timestamp scope uses: in the
    export, every probe completed within the hour it started, so the two cannot be told
    apart.
    """
    workgroup = _workgroup(line.ResourceId)
    return (
        query.account_id == line.SubAccountId
        and query.region == line.RegionId
        and (workgroup is None or query.workgroup == workgroup)
        and query.completed_at is not None
        and line.ChargePeriodStart <= query.completed_at < line.ChargePeriodEnd
    )


def _workgroup(resource_id: str | None) -> str | None:
    """The workgroup named by an Athena workgroup ARN, e.g. `...:workgroup/demo`."""
    fields = (resource_id or "").split(":", 5)
    if len(fields) < 6 or fields[0] != "arn" or fields[2] != "athena":
        return None
    kind, _, name = fields[5].partition("/")
    return name if kind == "workgroup" and name else None
