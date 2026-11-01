-- Incremental: later runs issue several Athena queries (temporary table, insert, cleanup),
-- which exercises one model mapping to many Query Executions.
{{ config(materialized='incremental', incremental_strategy='append') }}

select order_date, count(*) as orders, sum(amount) as revenue
from {{ ref('stg_orders') }}
{% if is_incremental() %}
where order_date > (select max(order_date) from {{ this }})
{% endif %}
group by order_date
