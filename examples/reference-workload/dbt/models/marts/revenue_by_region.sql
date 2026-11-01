select c.region, count(*) as orders, sum(o.amount) as revenue
from {{ ref('stg_orders') }} as o
join {{ ref('stg_customers') }} as c on o.customer_id = c.customer_id
group by c.region
