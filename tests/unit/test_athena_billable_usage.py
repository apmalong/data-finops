"""The Athena Billable Usage policy (#82, #91; spec 03, M4 structure 8).

`athena-billable-usage/2`, the current version, is worked from the real 2026-11 FOCUS
export (#91): a query that scanned nothing bills nothing, whatever its statement type, and
any other succeeded or cancelled query bills its scan rounded up to the next megabyte, at
least 10 MB. `athena-billable-usage/1` stays as it was, from AWS's published rule (failed
queries and 0-byte DDL free), so that earlier Calculation Runs stay reproducible.
Units are decimal (MB = 10^6 bytes, TB = 10^12 bytes), as the export confirms.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from data_finops.contracts.workload import QueryExecution
from data_finops.providers.athena_billing import (
    POLICY_VERSION,
    billable_bytes,
    bytes_in_consumed_unit,
)

MB = 1_000_000
V1 = "athena-billable-usage/1"


def _query(
    state: str = "SUCCEEDED", statement_type: str = "DML", scanned: int | None = 0
) -> QueryExecution:
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


@pytest.mark.parametrize(
    ("scanned", "billed"),
    [
        (1_000_000_000, 1_000_000_000),  # exact megabytes stay as they are
        (1_000_000_001, 1_001_000_000),  # one byte over rounds up a whole MB
        (25_400_000, 26_000_000),
        (10_000_001, 11_000_000),
        (9_999_999, 10_000_000),  # below the minimum
        (1, 10_000_000),
    ],
)
def test_succeeded_queries_bill_the_scan_rounded_up_to_a_megabyte_at_least_ten(
    scanned: int, billed: int
) -> None:
    assert billable_bytes(_query(scanned=scanned)) == billed
    assert billable_bytes(_query(scanned=scanned), V1) == billed


def test_the_current_version_is_two() -> None:
    assert POLICY_VERSION == "athena-billable-usage/2"


@pytest.mark.parametrize(
    ("state", "statement_type"),
    [
        ("SUCCEEDED", "DML"),  # a SELECT or INSERT that scanned nothing, or a reused result
        ("SUCCEEDED", "UTILITY"),
        ("SUCCEEDED", "DDL"),
        ("CANCELLED", "DML"),
    ],
)
def test_a_query_that_scanned_nothing_bills_nothing(state: str, statement_type: str) -> None:
    # The real export (#91): 0-byte SELECTs, a reused result, an INSERT and a VACUUM billed
    # nothing, while each CTAS, INSERT and OPTIMIZE that scanned any bytes billed 10 MB.
    assert billable_bytes(_query(state, statement_type, 0)) == 0


@pytest.mark.parametrize(
    ("statement_type", "billed"),
    [("DML", 10 * MB), ("UTILITY", 10 * MB), ("DDL", 0)],
)
def test_version_one_charged_the_minimum_for_nothing_but_ddl(
    statement_type: str, billed: int
) -> None:
    # Kept as it was, so a Calculation Run under /1 can be reproduced (OQ-24).
    assert billable_bytes(_query("SUCCEEDED", statement_type, 0), V1) == billed
    assert billable_bytes(_query("CANCELLED", statement_type, 0), V1) == billed


def test_an_unknown_version_is_refused() -> None:
    with pytest.raises(ValueError, match="athena-billable-usage/9"):
        billable_bytes(_query(), "athena-billable-usage/9")


@pytest.mark.parametrize(
    ("state", "statement_type", "scanned", "billed"),
    [
        ("FAILED", "DML", 50 * MB, 0),  # failed queries are not charged
        ("CANCELLED", "DML", 25_400_000, 26 * MB),  # charged for what was scanned
        ("QUEUED", "DML", 0, 0),  # not finished: nothing billed in this observation
        ("RUNNING", "DML", 3 * MB, 0),
        ("SUCCEEDED", "DDL", 0, 0),  # e.g. the uncommented `alter table` (OQ-22)
        ("SUCCEEDED", "DDL", 1_000_000_001, 1_001 * MB),  # CTAS: labelled DDL, scans data
        ("SUCCEEDED", "DDL", 734, 10 * MB),  # OPTIMIZE (#91): labelled DDL, billed its scan
    ],
)
def test_state_and_statement_type(
    state: str, statement_type: str, scanned: int, billed: int
) -> None:
    assert billable_bytes(_query(state, statement_type, scanned)) == billed


def test_a_finished_query_without_statistics_has_unknown_billable_usage() -> None:
    assert billable_bytes(_query("SUCCEEDED", scanned=None)) is None
    assert billable_bytes(_query("SUCCEEDED", "DDL", scanned=None)) is None
    assert billable_bytes(_query("FAILED", scanned=None)) == 0


def test_a_state_athena_does_not_document_is_refused() -> None:
    with pytest.raises(ValueError, match="'succeeded'"):
        billable_bytes(_query("succeeded"))


@pytest.mark.parametrize("unit", ["TB", "Terabytes"])
def test_billable_bytes_convert_to_the_line_unit_in_decimal_terabytes(unit: str) -> None:
    # The golden fixture: q-001's 1,000,000,000 bytes are the line's 0.001 TB.
    assert bytes_in_consumed_unit(1_000_000_000, unit) == Decimal("0.001")
    assert bytes_in_consumed_unit(26 * MB, unit) == Decimal("0.000026")
    assert bytes_in_consumed_unit(0, unit) == 0


def test_an_unknown_consumed_unit_is_refused() -> None:
    with pytest.raises(ValueError, match="GB"):
        bytes_in_consumed_unit(10 * MB, "GB")
