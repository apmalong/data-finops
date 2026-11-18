"""The Athena Billable Usage policy (#82, #91; spec 03, M4 structure 8).

`athena-billable-usage/2`, the current version, is worked from the real 2026-11 FOCUS
export (#91): a query that scanned nothing bills nothing, whatever its statement type, and
any other succeeded or cancelled query bills its scan rounded up to the next megabyte, at
least 10 MB. `athena-billable-usage/1` stays as it was, from AWS's published rule (failed
queries and 0-byte DDL free), so that earlier Calculation Runs stay reproducible.
Units are decimal (MB = 10^6 bytes, TB = 10^12 bytes), as the export confirms.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from data_finops.contracts.workload import QueryExecution
from data_finops.providers.athena_billing import (
    BILLABLE_USAGE_V1,
    BILLABLE_USAGE_V2,
    POLICY_VERSION,
    billable_bytes,
    billable_quantities,
    bytes_in_consumed_unit,
)

MB = 1_000_000
V1 = BILLABLE_USAGE_V1


def _query(
    state: str = "SUCCEEDED",
    statement_type: str = "DML",
    scanned: int | None = 0,
    completed: datetime | None = None,
) -> QueryExecution:
    return QueryExecution(
        provider="aws.athena",
        account_id="123456789012",
        region="us-east-1",
        query_execution_id="q",
        statement_type=statement_type,
        state=state,
        submitted_at=datetime(2026, 10, 1, tzinfo=UTC),
        completed_at=completed,
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


def test_the_current_version_is_three() -> None:
    assert POLICY_VERSION == "athena-billable-usage/3"


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


# `athena-billable-usage/3` (#310): the bill's TB is 2^40 bytes. A query is its raw scan, at
# least 1 x 10^-5 TB. Each UTC hour of completion is summed and rounded down to a multiple of
# 5 x 10^-6 TB, and its queries share that sum by their raw quantities.
TIB = 2**40
HOUR = datetime(2026, 11, 16, 4, tzinfo=UTC)


def _at(scanned: int, minute: int = 24, hour: datetime = HOUR) -> QueryExecution:
    return _query(scanned=scanned, completed=hour + timedelta(minutes=minute))


@pytest.mark.parametrize("unit", ["TB", "Terabytes"])
def test_a_tebibyte_is_one_billed_terabyte(unit: str) -> None:
    assert billable_quantities([_at(TIB)], unit) == [Decimal(1)]


def test_a_small_query_bills_the_minimum_of_a_hundred_thousandth() -> None:
    assert billable_quantities([_at(1)], "TB") == [Decimal("0.00001")]
    assert billable_quantities([_at(1), _at(734)], "TB") == [Decimal("0.00001")] * 2


def test_an_hour_is_rounded_down_and_its_queries_share_it_by_raw_quantity() -> None:
    # 1 byte bills 0.00001, 2^24 bytes 0.0000152587890625: the hour holds 0.0000252587890625,
    # rounded down to 0.000025.
    small, large = billable_quantities([_at(1), _at(2**24, minute=50)], "TB")
    assert small is not None and large is not None
    assert small + large == Decimal("0.000025")
    assert large / small == Decimal("1.52587890625")


def test_each_hour_of_completion_is_rounded_by_itself() -> None:
    # 14,293,651 bytes is 0.0000129999... TB: alone in an hour it rounds down to 0.00001.
    # Two in one hour would round 0.0000259999... down to 0.000025 instead.
    apart = [_at(14_293_651, minute=59), _at(14_293_651, minute=60)]
    assert billable_quantities(apart, "TB") == [Decimal("0.00001")] * 2
    together = billable_quantities([_at(14_293_651), _at(14_293_651, minute=30)], "TB")
    assert sum(q for q in together if q is not None) == Decimal("0.000025")


def test_the_demo_hour_of_2026_11_16_0400_bills_what_the_export_billed() -> None:
    # The owner's export (#310): these 22 queries completed in 04:00-05:00 UTC, and the hour
    # is the line's 0.00256 TB less its three other hours of 0.000575 TB each.
    scans = [
        156_840, 14_951_491, 20_134_292, 18_616_756, 5_943_691, 15_939_340, 14_715_050,
        11_400_896, 74_759_569, 244_349_213, 169_589_644, 59_027_597, 74_752_338, 14_951_491,
        20_134_292, 156_840, 20_134_268, 20_134_268, 15_838_349, 51_432_103, 5_943_691,
        18_616_756,
    ]  # fmt: skip
    hour = billable_quantities([_at(scan) for scan in scans], "TB")
    assert sum(q for q in hour if q is not None) == Decimal("0.000835")


def test_version_three_keeps_the_states_that_bill_nothing_or_are_unknown() -> None:
    nothing = [_query("FAILED", scanned=50 * MB, completed=HOUR), _at(0)]
    assert billable_quantities(nothing, "TB") == [Decimal(0), Decimal(0)]
    assert billable_quantities([_query(scanned=None, completed=HOUR)], "TB") == [None]


def test_earlier_versions_give_their_bytes_in_the_line_unit() -> None:
    queries = [_at(1_000_000_000), _at(1), _query("FAILED", scanned=50 * MB, completed=HOUR)]
    expected = [Decimal("0.001"), Decimal("0.00001"), Decimal(0)]
    assert billable_quantities(queries, "TB", BILLABLE_USAGE_V2) == expected
    assert billable_quantities(queries, "TB", V1) == expected


def test_version_three_refuses_an_unknown_consumed_unit() -> None:
    with pytest.raises(ValueError, match="GB"):
        billable_quantities([_at(1)], "GB")
