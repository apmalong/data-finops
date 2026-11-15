"""The simulated workload's DAGs and dbt models (#226; spec 09 §The simulated orchestrator; ADR
0030): which DAG runs when, its tasks, the models of each task and the real SQL of each model.

Each statement runs in the `data-finops-demo` workgroup against the demo dataset (#225), so
its scan, and its cost, is real. `{tenant}` is one of the dataset's tenants, chosen by the
plan; nothing else is put in the SQL. The statements only read: the simulator's role has no
grant to write a table.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

PROJECT = "demo_shop"
"""The demo's dbt project. A model's `node_id` is `model.demo_shop.<model>`."""
NAMESPACE = "demo-airflow"
"""The OpenLineage namespace of the simulated Airflow, as `reference-airflow` was."""
TENANTS = ("northwind", "contoso", "fabrikam", "tailspin", "wingtip", "adventure-works")
"""The tenants of the demo dataset (`dataset.TENANTS`): believable and fictional."""

MODELS: dict[str, tuple[str, ...]] = {
    "stg_customers": (
        "select country, segment, count(*) as customers from customers "
        "where tenant_id = '{tenant}' group by country, segment",
    ),
    "stg_orders": (
        "select max(order_ts) as latest from orders where tenant_id = '{tenant}'",
        "select date_trunc('day', order_ts) as day, status, count(*) as orders, "
        "sum(amount) as revenue from orders where tenant_id = '{tenant}' group by 1, 2",
    ),
    "orders_daily": (
        "select date_trunc('day', order_ts) as day, count(*) as orders, sum(amount) as revenue "
        "from orders group by 1",
    ),
    "revenue_by_tenant": (
        "select tenant_id, status, sum(amount) as revenue from orders group by tenant_id, status",
    ),
    "customer_ltv": (
        "select c.segment, c.country, count(distinct o.customer_id) as buyers, "
        "sum(o.amount) as revenue from orders o join customers c "
        "on o.customer_id = c.customer_id where o.tenant_id = '{tenant}' group by 1, 2",
        "select customer_id, sum(amount) as lifetime_value from orders "
        "where tenant_id = '{tenant}' group by customer_id order by 2 desc limit 100",
    ),
    "stg_events": (
        "select event_type, page, count(*) as events from events "
        "where tenant_id = '{tenant}' group by event_type, page",
    ),
    "sessions": (
        "select max(event_ts) as latest from events where tenant_id = '{tenant}'",
        "select session_id, count(*) as events, min(event_ts) as started, "
        "max(event_ts) as ended from events where tenant_id = '{tenant}' group by session_id",
        "select count(distinct session_id) as sessions from events",
    ),
    "page_funnel": (
        "select page, event_type, count(distinct customer_id) as visitors from events "
        "where tenant_id = '{tenant}' group by page, event_type",
    ),
    "event_counts": (
        "select date_trunc('hour', event_ts) as hour, event_type, count(*) as events "
        "from events group by 1, 2",
    ),
    "finance_revenue": (
        "select date_trunc('month', order_ts) as month, tenant_id, sum(amount) as revenue "
        "from orders where status in ('paid', 'shipped', 'delivered') group by 1, 2",
    ),
    "finance_refunds": (
        "select date_trunc('month', order_ts) as month, tenant_id, count(*) as returns, "
        "sum(amount) as refunded from orders where status = 'returned' group by 1, 2",
    ),
    "tenant_invoices": (
        "select o.tenant_id, c.segment, count(*) as orders, sum(o.amount) as billed "
        "from orders o join customers c on o.customer_id = c.customer_id "
        "where o.tenant_id = '{tenant}' group by 1, 2",
        "select tenant_id, count(distinct customer_id) as active_customers from events "
        "where tenant_id = '{tenant}' group by tenant_id",
    ),
}
"""Each model's statements, one to three, scanning from under 10 MB to a few hundred MB."""


@dataclass(frozen=True)
class Task:
    name: str
    models: tuple[str, ...]
    """Two to five models, run in this order by one dbt invocation."""


@dataclass(frozen=True)
class Dag:
    name: str
    team: str
    """The fictional team that owns the DAG: a Cost Dimension in the query comment."""
    tasks: tuple[Task, ...]
    due: Callable[[datetime], bool]
    """Whether a start at this time runs the DAG."""


def _first_start_of_day(start: datetime) -> bool:
    return start.hour < 6


DAGS: tuple[Dag, ...] = (
    Dag(
        "daily_orders",
        "commerce",
        (
            Task("stage", ("stg_customers", "stg_orders")),
            Task("transform", ("orders_daily", "revenue_by_tenant", "customer_ltv")),
        ),
        _first_start_of_day,
    ),
    Dag(
        "hourly_events",
        "growth",
        (
            Task("stage", ("stg_events", "sessions")),
            Task("metrics", ("page_funnel", "event_counts")),
        ),
        lambda start: True,
    ),
    Dag(
        "weekly_finance",
        "finance",
        (
            Task("stage", ("stg_orders", "stg_customers")),
            Task("close", ("finance_revenue", "finance_refunds", "tenant_invoices")),
            Task("publish", ("revenue_by_tenant", "orders_daily")),
        ),
        lambda start: start.weekday() == 0 and _first_start_of_day(start),
    ),
)
"""`daily_orders` once a day, `hourly_events` at each start (each six hours by default) and
`weekly_finance` on Mondays (spec 09)."""
