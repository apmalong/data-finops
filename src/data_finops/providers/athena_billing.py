"""Athena on-demand billing policy (spec 03, M4 structure 8; ADR 0017).

Provider policy, never core logic, in two independently versioned parts: how much of a
Query Execution Athena bills (`POLICY_VERSION`), and which Billing Line it is billed in
(billing scope, ADR 0023; `SCOPE_POLICY_VERSION`). The engine in `attribution` takes
both results as given; a Calculation Run records both versions (#88).

Documented by AWS: data scanned is rounded up to the nearest megabyte with a 10 MB
minimum per query; failed queries and DDL are not charged; cancelled queries are charged
for the data scanned. Provisional, to be checked against a real FOCUS export (#91):
- units are decimal (MB = 10^6 bytes, TB = 10^12 bytes), and ConsumedUnit reads `TB` or
  `Terabytes`;
- `DataScannedInBytes` is the raw scan, before the minimum and rounding;
- queries answered from reused results bill like any other;
- CTAS, INSERT INTO, OPTIMIZE and VACUUM bill their scan (CTAS is labelled `DDL`).
"""

from decimal import Decimal

from data_finops.contracts.billing import FocusBillingLine
from data_finops.contracts.workload import QueryExecution

POLICY_VERSION = "athena-billable-usage/1"
SCOPE_POLICY_VERSION = "athena-billing-scope/1"

_MB = 1_000_000
_MINIMUM = 10 * _MB
_BILLED_STATES = frozenset({"SUCCEEDED", "CANCELLED"})
_UNBILLED_STATES = frozenset({"QUEUED", "RUNNING", "FAILED"})
_BYTES_PER_UNIT = {"TB": 10**12, "Terabytes": 10**12}


def billable_bytes(query: QueryExecution) -> int | None:
    """The bytes Athena bills for `query`, or None when a billed query reported no scan.

    Succeeded and cancelled queries bill their scan rounded up to a megabyte, at least
    10 MB. Failed queries bill nothing, and so do queued or running ones in this
    observation (a later observation of the finished query is stored separately). A DDL
    statement bills nothing only if it scanned nothing: Athena labels CTAS `DDL` too.
    Raises ValueError for a state Athena does not document.
    """
    if query.state in _UNBILLED_STATES:
        return 0
    if query.state not in _BILLED_STATES:
        raise ValueError(f"{query.query_execution_id}: unknown Athena state {query.state!r}")
    if query.scanned_bytes is None:
        return None
    if query.statement_type == "DDL" and query.scanned_bytes == 0:
        return 0
    return max(-(-query.scanned_bytes // _MB) * _MB, _MINIMUM)


def bytes_in_consumed_unit(billable: int, consumed_unit: str) -> Decimal:
    """`billable` bytes in a Billing Line's ConsumedUnit, so Coverage compares like with like."""
    if consumed_unit not in _BYTES_PER_UNIT:
        raise ValueError(f"unknown Athena ConsumedUnit {consumed_unit!r}")
    return Decimal(billable) / _BYTES_PER_UNIT[consumed_unit]


def in_billing_scope(query: QueryExecution, line: FocusBillingLine) -> bool:
    """Whether `query` falls in the scope Athena billed `line` for (ADR 0023).

    Accounting scope, never Evidence: the query ran in the line's usage account (FOCUS
    SubAccountId, not the paying BillingAccountId) and region, in the line's workgroup if
    the line names one, and completed within the line's charge period, start inclusive.
    A query that has not completed is not yet billed. Link Outcomes are not an input:
    linked and unlinked queries are scoped alike. Provisional until #91: completion
    time is the timestamp Athena bills by, and a line names its workgroup by a workgroup
    ARN in ResourceId; a line naming none does not restrict by workgroup.
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
