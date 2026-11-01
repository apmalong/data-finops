select customer_id, region
from {{ ref('raw_customers') }}
