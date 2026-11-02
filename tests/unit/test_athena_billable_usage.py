"""The Athena Billable Usage policy athena-billable-usage/1 (#82; spec 03, M4 structure 8).

Expected values are worked from AWS's published rule: data scanned is rounded up to the
nearest megabyte with a 10 MB minimum per query; failed queries and DDL are not charged.
Units are decimal (MB = 10^6 bytes, TB = 10^12 bytes), provisional until #91.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from data_finops.contracts.workload import QueryExecution
from data_finops.providers.athena_billing import POLICY_VERSION, billable_bytes, in_unit

MB = 1_000_000


def _query(state: str = "SUCCEEDED", statement_type: str = "DML", scanned: int | None = 0):
    return QueryExecution(
        provider="aws.athena",
        account_id="123456789012",
        region="us-east-1",
        query_execution_id="q",
        statement_type=statement_type,
        state=state,
        submitted_at=datetime(2026, 10, 1, tzinfo=UTC),
        scanned_bytes=scanned,
    )


def test_the_policy_is_versioned() -> None:
    assert POLICY_VERSION == "athena-billable-usage/1"


@pytest.mark.parametrize(
    ("scanned", "billed"),
    [
        (1_000_000_000, 1_000_000_000),  # exact megabytes stay as they are
        (1_000_000_001, 1_001_000_000),  # one byte over rounds up a whole MB
        (25_400_000, 26_000_000),
        (10_000_001, 11_000_000),
        (9_999_999, 10_000_000),  # below the minimum
        (1, 10_000_000),
        (0, 10_000_000),  # a query that scanned nothing still pays the minimum
    ],
)
def test_succeeded_queries_bill_the_scan_rounded_up_to_a_megabyte_at_least_ten(
    scanned: int, billed: int
) -> None:
    assert billable_bytes(_query(scanned=scanned)) == billed


@pytest.mark.parametrize(
    ("state", "statement_type", "scanned", "billed"),
    [
        ("FAILED", "DML", 50 * MB, 0),  # failed queries are not charged
        ("CANCELLED", "DML", 25_400_000, 26 * MB),  # charged for what was scanned
        ("CANCELLED", "DML", 0, 10 * MB),
        ("QUEUED", "DML", 0, 0),  # not finished: nothing billed in this observation
        ("RUNNING", "DML", 3 * MB, 0),
        ("SUCCEEDED", "DDL", 0, 0),  # e.g. the uncommented `alter table` (OQ-22)
        ("SUCCEEDED", "DDL", 1_000_000_001, 1_001 * MB),  # CTAS: labelled DDL, scans data
        ("SUCCEEDED", "UTILITY", 0, 10 * MB),
    ],
)
def test_state_and_statement_type(
    state: str, statement_type: str, scanned: int, billed: int
) -> None:
    assert billable_bytes(_query(state, statement_type, scanned)) == billed


def test_a_finished_query_without_statistics_has_unknown_billable_usage() -> None:
    assert billable_bytes(_query("SUCCEEDED", scanned=None)) is None
    assert billable_bytes(_query("FAILED", scanned=None)) == 0


@pytest.mark.parametrize("unit", ["TB", "Terabytes"])
def test_billable_bytes_convert_to_the_line_unit_in_decimal_terabytes(unit: str) -> None:
    # The golden fixture: q-001's 1,000,000,000 bytes are the line's 0.001 TB.
    assert in_unit(1_000_000_000, unit) == Decimal("0.001")
    assert in_unit(26 * MB, unit) == Decimal("0.000026")
    assert in_unit(0, unit) == 0


def test_an_unknown_consumed_unit_is_refused() -> None:
    with pytest.raises(ValueError, match="GB"):
        in_unit(10 * MB, "GB")
