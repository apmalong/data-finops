"""Athena billing scope (#83; ADR 0023): which Query Executions belong to a Billing Line.

Accounting scope, not Evidence: the query ran in the line's usage account (FOCUS
SubAccountId) and region, and completed within the line's charge period. Expected values
are worked from the fixture line: account 123456789012, us-east-1, 2026-10-01 to 10-02.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_finops.billing.focus import load_export
from data_finops.contracts.workload import QueryCorrelation, QueryExecution
from data_finops.providers.athena_billing import in_billing_scope

ROOT = Path(__file__).resolve().parents[2]
LINE = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json").lines[0]


def _query(
    completed_at: datetime | None = datetime(2026, 10, 1, 1, tzinfo=UTC),
    account_id: str = "123456789012",
    region: str = "us-east-1",
    workgroup: str | None = "demo",
) -> QueryExecution:
    return QueryExecution(
        provider="aws.athena",
        account_id=account_id,
        region=region,
        query_execution_id="q",
        workgroup=workgroup,
        state="SUCCEEDED",
        submitted_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC),
        completed_at=completed_at,
    )


def test_a_query_in_the_lines_account_region_and_charge_period_is_in_scope() -> None:
    assert in_billing_scope(_query(), LINE)


@pytest.mark.parametrize(
    "query",
    [
        _query(account_id="210987654321"),
        _query(region="eu-west-1"),
        _query(completed_at=datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)),
        _query(completed_at=datetime(2026, 10, 2, tzinfo=UTC)),  # the end is exclusive
        _query(completed_at=None),  # not finished, so not yet billed
    ],
    ids=["other-account", "other-region", "before", "at-end", "not-completed"],
)
def test_a_query_outside_any_billing_dimension_is_out_of_scope(query: QueryExecution) -> None:
    assert not in_billing_scope(query, LINE)


def test_the_charge_period_start_is_inclusive() -> None:
    assert in_billing_scope(_query(completed_at=datetime(2026, 10, 1, tzinfo=UTC)), LINE)


def test_the_usage_account_decides_not_the_payer() -> None:
    # Consolidated billing: the payer is BillingAccountId; queries run in SubAccountId.
    line = LINE.model_copy(update={"BillingAccountId": "999999999999"})
    assert in_billing_scope(_query(), line)
    assert not in_billing_scope(_query(account_id="999999999999"), line)


def test_a_line_naming_a_workgroup_scopes_only_that_workgroup() -> None:
    # The fixture line's ResourceId is the workgroup ARN `...:workgroup/demo`.
    assert in_billing_scope(_query(workgroup="demo"), LINE)
    assert not in_billing_scope(_query(workgroup="demo-link-outcomes"), LINE)
    assert not in_billing_scope(_query(workgroup=None), LINE)


def test_a_workgroup_arn_in_any_aws_partition_names_the_workgroup() -> None:
    line = LINE.model_copy(
        update={"ResourceId": "arn:aws-cn:athena:us-east-1:123456789012:workgroup/demo"}
    )
    assert not in_billing_scope(_query(workgroup="analysts"), line)


@pytest.mark.parametrize(
    "resource_id",
    [
        None,
        "",
        "arn:aws:s3:::demo-lake",
        "arn:aws:athena:us-east-1:123456789012:datacatalog/x",
        "arn:aws:athena:us-east-1:123456789012:workgroup/",
        "arn:aws:glue:us-east-1:123456789012:workgroup/demo",  # not Athena
        "urn:aws:athena:us-east-1:123456789012:workgroup/demo",  # not an ARN
        "workgroup/demo",
        "arn:aws:athena",  # truncated
    ],
)
def test_a_line_naming_no_workgroup_does_not_restrict_by_workgroup(
    resource_id: str | None,
) -> None:
    # Provisional (#91): whether AWS splits Athena rows per workgroup is not yet observed.
    line = LINE.model_copy(update={"ResourceId": resource_id})
    assert in_billing_scope(_query(workgroup="analysts"), line)


def test_a_line_without_its_usage_account_or_region_scopes_nothing() -> None:
    assert not in_billing_scope(_query(), LINE.model_copy(update={"SubAccountId": None}))
    assert not in_billing_scope(_query(), LINE.model_copy(update={"RegionId": None}))


def test_a_trusted_query_comment_does_not_decide_scope() -> None:
    # ADR 0023: scope is accounting scope, not Evidence; uncommented queries count too.
    commented = _query().model_copy(
        update={"correlation": QueryCorrelation(node_id="model.demo.orders", invocation_id="i")}
    )
    assert in_billing_scope(commented, LINE)
    assert in_billing_scope(_query(), LINE)
