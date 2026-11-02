"""Field allowlists for the workload anonymizer (#36): dbt artifacts and OpenLineage events.

Each spec maps a known field to how its value is treated; any other field, and any value
whose shape does not match its spec, is dropped and counted. Built from dbt-core 1.12.6,
openlineage-dbt 1.54.0 and apache-airflow-providers-openlineage 2.20.1 (Airflow 3.3.2) output
plus the documented dbt node, source and test fields; a new upstream field is dropped until it
is reviewed here.
- KEEP: kept, strings still pass the bucket, account and path rules;
- TEXT: free text; strings become pseudonyms, maps and lists of maps are emptied;
- SQL: cut to a dbt comment and the statement's leading keywords;
- URI: credentials removed and hosts other than AWS public endpoints pseudonymized;
- EMPTY: replaced by an empty map (collections the product does not read);
- a dict: a nested spec; Each(spec): a map of IDs or names to entries; Items(spec): a list.
Known limits: schema, relation and node names are kept for correlation, so a developer
schema such as `dbt_<username>` survives; seed checksums may hold a project-relative path.
"""

from dataclasses import dataclass
from typing import Any

KEEP = "keep"
TEXT = "text"
SQL = "sql"
URI = "uri"
EMPTY = "empty"


@dataclass(frozen=True)
class Each:
    spec: Any


@dataclass(frozen=True)
class Items:
    spec: Any


def _keep(*names: str, **rules: Any) -> dict[str, Any]:
    """A spec keeping `names` as they are, plus fields with their own rules."""
    return dict.fromkeys(names, KEEP) | rules


def _facet(**rules: Any) -> dict[str, Any]:
    """An OpenLineage facet: its own fields plus the standard `_producer` and `_schemaURL`."""
    return _keep("_deleted", "_producer", "_schemaURL", **rules)


DBT_METADATA = _keep(
    "adapter_type",
    "dbt_schema_version",
    "dbt_version",
    "generated_at",
    "invocation_id",
    "invocation_started_at",
    "project_name",
    "quoting",
    "run_started_at",
    "send_anonymous_usage_stats",
    env=KEEP,  # keys and values pseudonymized in _dbt, before the spec applies
    project_id=KEEP,  # pseudonymized in _dbt
    user_id=KEEP,  # cleared in _dbt
)

HOOK = Items(_keep("index", "transaction", sql=SQL))
FRESHNESS = _keep("error_after", "warn_after", filter=SQL)
CONSTRAINTS = Items(
    _keep(
        "columns",
        "name",
        "to",
        "to_columns",
        "type",
        "warn_unenforced",
        "warn_unsupported",
        expression=SQL,
    )
)

DBT_CONFIG = _keep(
    "access",
    "alias",
    "batch_size",
    "begin",
    "bucket_count",
    "bucketed_by",
    "column_types",
    "concurrent_batches",
    "contract",
    "database",
    "delimiter",
    "docs",
    "enabled",
    "event_time",
    "external_location",
    "field_delimiter",
    "file_format",
    "format",
    "full_refresh",
    "group",
    "incremental_strategy",
    "latest_version_pointer",
    "lookback",
    "materialized",
    "on_configuration_change",
    "on_error",
    "on_schema_change",
    "packages",
    "partitioned_by",
    "persist_docs",
    "quote_columns",
    "quoting",
    "s3_data_dir",
    "s3_data_naming",
    "schema",
    "static_analysis",
    "table_type",
    "unique_key",
    "work_group",
    "write_compression",
    freshness=FRESHNESS,
    grants=TEXT,  # grantee names
    meta=TEXT,
    table_properties=TEXT,
    tags=TEXT,
    **{"post-hook": HOOK, "pre-hook": HOOK},
)

COLUMN = _keep(
    "data_type",
    "granularity",
    "name",
    "quote",
    config={"meta": TEXT, "tags": TEXT},
    constraints=CONSTRAINTS,
    description=TEXT,
    doc_blocks=TEXT,
    meta=TEXT,
    tags=TEXT,
)

DBT_NODE = _keep(
    "access",
    "alias",
    "attached_node",
    "build_path",
    "checksum",
    "column_name",
    "compiled",
    "compiled_path",
    "contract",
    "created_at",
    "database",
    "depends_on",
    "deprecation_date",
    "docs",
    "extra_ctes_injected",
    "file_key_name",
    "fqn",
    "functions",
    "group",
    "language",
    "latest_version",
    "metrics",
    "name",
    "original_file_path",
    "package_name",
    "patch_path",
    "path",
    "primary_key",
    "refs",
    "relation_name",
    "resource_type",
    "root_path",
    "schema",
    "sources",
    "time_spine",
    "unique_id",
    "version",
    columns=Each(COLUMN),
    compiled_code=SQL,
    config=DBT_CONFIG,
    constraints=CONSTRAINTS,
    description=TEXT,
    doc_blocks=TEXT,
    extra_ctes=Items({"id": KEEP, "sql": SQL}),
    meta=TEXT,
    raw_code=SQL,
    tags=TEXT,
    test_metadata={"name": KEEP, "namespace": KEEP, "kwargs": TEXT},
    unrendered_config=DBT_CONFIG,
)

DBT_MACRO = _keep(
    "created_at",
    "depends_on",
    "docs",
    "name",
    "original_file_path",
    "package_name",
    "patch_path",
    "path",
    "resource_type",
    "supported_languages",
    "unique_id",
    arguments=Items({"name": KEEP, "type": KEEP, "description": TEXT}),
    config={"docs": KEEP, "meta": TEXT},
    description=TEXT,
    macro_sql=SQL,
    meta=TEXT,
)

DBT_DOC = _keep(
    "name",
    "original_file_path",
    "package_name",
    "path",
    "resource_type",
    "unique_id",
    block_contents=TEXT,
)

DBT_SOURCE = _keep(
    "created_at",
    "database",
    "event_time",
    "fqn",
    "identifier",
    "loaded_at_field",
    "loader",
    "name",
    "original_file_path",
    "package_name",
    "patch_path",
    "path",
    "quoting",
    "relation_name",
    "resource_type",
    "schema",
    "source_name",
    "unique_id",
    "unrendered_database",
    "unrendered_schema",
    columns=Each(COLUMN),
    config=DBT_CONFIG,
    description=TEXT,
    doc_blocks=TEXT,
    external=TEXT,  # locations, partitions and their descriptions
    freshness=FRESHNESS,
    loaded_at_query=SQL,
    meta=TEXT,
    source_description=TEXT,
    source_meta=TEXT,
    tags=TEXT,
    unrendered_config=DBT_CONFIG,
)

MANIFEST = {
    "child_map": KEEP,
    "disabled": EMPTY,
    "docs": Each(DBT_DOC),
    "exposures": EMPTY,
    "functions": EMPTY,
    "group_map": KEEP,
    "groups": EMPTY,  # owners' names and emails
    "macros": Each(DBT_MACRO),
    "metadata": DBT_METADATA,
    "metrics": EMPTY,
    "nodes": Each(DBT_NODE),
    "parent_map": KEEP,
    "saved_queries": EMPTY,
    "selectors": EMPTY,
    "semantic_models": EMPTY,
    "sources": Each(DBT_SOURCE),
    "unit_tests": EMPTY,
}

RUN_RESULT = _keep(
    "batch_results",
    "compiled",
    "execution_time",
    "failures",
    "overload_results",
    "relation_name",
    "status",
    "thread_id",
    "timing",
    "unique_id",
    adapter_response=_keep(
        "code", "data_scanned_in_bytes", "query_id", "rows_affected", _message=TEXT
    ),
    compiled_code=SQL,
    message=TEXT,
)

RUN_RESULTS = {
    "args": KEEP,  # filtered by ARGS_TEXT in _dbt, before the spec applies
    "elapsed_time": KEEP,
    "metadata": DBT_METADATA,
    "results": Items(RUN_RESULT),
}

# Airflow provider facets (#76). Kept: DAG, task and run identity, operator classes, run
# types, times, states and retry settings. Pseudonymized as free text: owners and triggering
# users (names or emails), DAG file locations, DAG tags, inlets and outlets (dataset strings).
# Dropped: UI colours and labels, and `unknownSourceAttribute`, which repeats the task.
AIRFLOW_DAG = _keep(
    "dag_id", "start_date", "timetable_summary", fileloc=TEXT, owner=TEXT, tags=TEXT
)
AIRFLOW_DAG_RUN = _keep(
    "clear_number",
    "dag_bundle_name",
    "dag_id",
    "dag_version_id",
    "dag_version_number",
    "duration",
    "end_date",
    "run_after",
    "run_id",
    "run_type",
    "start_date",
    "triggered_by",
    triggering_user_name=TEXT,
)
AIRFLOW_TASK = _keep(
    "depends_on_past",
    "downstream_task_ids",
    "ignore_first_depends_on_past",
    "mapped",
    "multiple_outputs",
    "operator_class",
    "operator_class_path",
    "operator_provider_version",
    "priority_weight",
    "queue",
    "retries",
    "retry_exponential_backoff",
    "task_id",
    "trigger_rule",
    "upstream_task_ids",
    "wait_for_downstream",
    "wait_for_past_depends_before_skipping",
    inlets=TEXT,
    outlets=TEXT,
    owner=TEXT,
)

RUN_FACETS = {
    "airflow": _facet(
        dag=AIRFLOW_DAG,
        dagRun=AIRFLOW_DAG_RUN,
        task=AIRFLOW_TASK,
        taskInstance=_keep("dag_bundle_name", "try_number", log_url=URI),
        taskUuid=KEEP,
    ),
    "airflowDagRun": _facet(dag=AIRFLOW_DAG, dagRun=AIRFLOW_DAG_RUN),
    "airflowState": _facet(dagRunState=KEEP, tasksDuration=Each(KEEP), tasksState=Each(KEEP)),
    "nominalTime": _facet(nominalEndTime=KEEP, nominalStartTime=KEEP),
    "dbt_run": _facet(
        dbt_runtime=KEEP,
        invocation_id=KEEP,
        profile_name=KEEP,
        project_name=KEEP,
        project_version=KEEP,
    ),
    "dbt_version": _facet(version=KEEP),
    "errorMessage": _facet(message=TEXT, programmingLanguage=KEEP, stackTrace=TEXT),
    "parent": _facet(job=KEEP, root=KEEP, run=KEEP),
    "processing_engine": _facet(name=KEEP, openlineageAdapterVersion=KEEP, version=KEEP),
    "tags": _facet(tags=Items({"key": KEEP, "source": KEEP, "value": TEXT})),
}

JOB_FACETS = {
    "airflow": _facet(tasks=Each(_keep("emits_ol_events", "is_setup", "is_teardown", "operator"))),
    "ownership": _facet(owners=Items({"name": TEXT, "type": KEEP})),
    "sourceCode": _facet(language=KEEP, sourceCode=TEXT),
    "tags": _facet(tags=Items({"key": KEEP, "source": KEEP, "value": TEXT})),
    "dbt_node": _facet(
        alias=KEEP, database=KEEP, original_file_path=KEEP, schema=KEEP, unique_id=KEEP
    ),
    "documentation": _facet(description=TEXT),
    "jobType": _facet(integration=KEEP, jobType=KEEP, processingType=KEEP),
    "sql": _facet(dialect=KEEP, query=SQL),
}

TRANSFORMATIONS = Items(_keep("masking", "subtype", "type", description=TEXT))
INPUT_FIELD = _keep("field", "name", namespace=URI, transformations=TRANSFORMATIONS)

DATASET = {
    "facets": {
        "columnLineage": _facet(
            dataset=Items(INPUT_FIELD),
            fields=Each({"inputFields": Items(INPUT_FIELD), "transformations": TRANSFORMATIONS}),
        ),
        "dataSource": _facet(name=URI, uri=URI),
        "dbt_model": _facet(
            config=DBT_CONFIG | {"incremental": {"strategy": KEEP, "on_schema_change": KEEP}}
        ),
        "documentation": _facet(description=TEXT),
        "schema": _facet(fields=Items({"name": KEEP, "type": KEEP, "description": TEXT})),
        "symlinks": _facet(identifiers=Items({"name": KEEP, "namespace": URI, "type": KEEP})),
    },
    "inputFacets": {},  # none reviewed yet: every input facet is dropped
    "name": KEEP,
    "namespace": URI,
    "outputFacets": {"outputStatistics": _facet(fileCount=KEEP, rowCount=KEEP, size=KEEP)},
}

OPENLINEAGE_EVENT = {
    "eventTime": KEEP,
    "eventType": KEEP,
    "inputs": Items(DATASET),
    "job": {"facets": JOB_FACETS, "name": KEEP, "namespace": KEEP},
    "outputs": Items(DATASET),
    "producer": KEEP,
    "run": {"facets": RUN_FACETS, "runId": KEEP},
    "schemaURL": KEEP,
}
