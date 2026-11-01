{#- JSON query comment for data-finops correlation (spec 04). node is none for queries outside a node. -#}
{% macro data_finops_query_comment(node) -%}
  {%- set comment = {
      "app": "dbt",
      "dbt_version": dbt_version,
      "invocation_id": invocation_id,
      "node_id": node.unique_id if node is not none else none,
      "target_name": target.name,
  } -%}
  {{- return(tojson(comment)) -}}
{%- endmacro %}
