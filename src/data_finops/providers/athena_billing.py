"""Athena on-demand billing policy (spec 03, M4 structure 8; ADR 0017).

Provider pricing, never core logic: how much of a Query Execution Athena bills. The
engine in `attribution` takes the result as given; every Allocation built from it
records `POLICY_VERSION`.

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

from data_finops.contracts.workload import QueryExecution

POLICY_VERSION = "athena-billable-usage/1"

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
