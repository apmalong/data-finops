"""Field allowlists for the workload anonymizer (#36): dbt artifacts and OpenLineage events.

Each spec maps a known field to how its value is treated; any other field is dropped and
counted. Built from dbt-core 1.12.6 and openlineage-dbt 1.54.0 output plus the documented
dbt node, source and test fields; a new upstream field is dropped until it is reviewed here.
- KEEP: kept, strings still pass the bucket, account and path rules;
- TEXT: free text; strings become pseudonyms, maps and lists of maps are emptied;
- SQL: cut to a dbt comment and the statement's leading keywords;
- EMPTY: the value is replaced by an empty map (collections the product does not read);
- a dict: a nested spec; Each(spec): a map of IDs or names to entries; Items(spec): a list.
"""

from dataclasses import dataclass
from typing import Any

KEEP = "keep"
TEXT = "text"
SQL = "sql"
EMPTY = "empty"


@dataclass(frozen=True)
class Each:
    spec: Any


@dataclass(frozen=True)
class Items:
    spec: Any


def _facet(**fields: Any) -> dict[str, Any]:
    """An OpenLineage facet: its own fields plus the standard `_producer` and `_schemaURL`."""
    return {"_producer": KEEP, "_schemaURL": KEEP, "_deleted": KEEP, **fields}


DBT_METADATA = {
    "adapter_type": KEEP,
    "dbt_schema_version": KEEP,
    "dbt_version": KEEP,
    "env": KEEP,  # values are pseudonymized before the spec applies
    "generated_at": KEEP,
    "invocation_id": KEEP,
    "invocation_started_at": KEEP,
    "project_id": KEEP,  # pseudonymized before the spec applies
    "project_name": KEEP,
    "quoting": KEEP,
    "run_started_at": KEEP,
    "send_anonymous_usage_stats": KEEP,
    "user_id": KEEP,  # cleared before the spec applies
}

HOOK = Items({"sql": SQL, "transaction": KEEP, "index": KEEP})

DBT_CONFIG = {
    key: KEEP
    for key in (
        "access",
        "alias",
        "batch_size",
        "begin",
        "bucketed_by",
        "bucket_count",
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
        "freshness",
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
    )
} | {
    "grants": TEXT,  # grantee names
    "meta": TEXT,
    "post-hook": HOOK,
    "pre-hook": HOOK,
    "table_properties": TEXT,
    "tags": TEXT,
}

COLUMN = {
    "config": {"meta": TEXT, "tags": TEXT},
    "constraints": KEEP,
    "data_type": KEEP,
    "description": TEXT,
    "doc_blocks": TEXT,
    "granularity": KEEP,
    "meta": TEXT,
    "name": KEEP,
    "quote": KEEP,
    "tags": TEXT,
}

DBT_NODE = {
    key: KEEP
    for key in (
        "access",
        "alias",
        "attached_node",
        "build_path",
        "checksum",
        "column_name",
        "compiled",
        "compiled_path",
        "constraints",
        "contract",
        "created_at",
        "database",
        "depends_on",
        "deprecation_date",
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
    )
} | {
    "columns": Each(COLUMN),
    "compiled_code": SQL,
    "config": DBT_CONFIG,
    "description": TEXT,
    "doc_blocks": TEXT,
    "docs": KEEP,
    "extra_ctes": Items({"id": KEEP, "sql": SQL}),
    "meta": TEXT,
    "raw_code": SQL,
    "tags": TEXT,
    "test_metadata": {"name": KEEP, "namespace": KEEP, "kwargs": TEXT},
    "unrendered_config": DBT_CONFIG,
}

DBT_MACRO = {
    key: KEEP
    for key in (
        "arguments",
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
    )
} | {"config": {"docs": KEEP, "meta": TEXT}, "description": TEXT, "macro_sql": SQL, "meta": TEXT}

DBT_DOC = {
    key: KEEP
    for key in ("name", "original_file_path", "package_name", "path", "resource_type", "unique_id")
} | {"block_contents": TEXT}

DBT_SOURCE = {
    key: KEEP
    for key in (
        "created_at",
        "database",
        "event_time",
        "external",
        "fqn",
        "freshness",
        "identifier",
        "loaded_at_field",
        "loaded_at_query",
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
        "unrendered_config",
        "unrendered_database",
        "unrendered_schema",
    )
} | {
    "columns": Each(COLUMN),
    "config": DBT_CONFIG,
    "description": TEXT,
    "doc_blocks": TEXT,
    "meta": TEXT,
    "source_description": TEXT,
    "source_meta": TEXT,
    "tags": TEXT,
}

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

RUN_RESULT = {
    key: KEEP
    for key in (
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
    )
} | {
    "adapter_response": {
        "_message": TEXT,
        "code": KEEP,
        "data_scanned_in_bytes": KEEP,
        "query_id": KEEP,
        "rows_affected": KEEP,
    },
    "compiled_code": SQL,
    "message": TEXT,
}

RUN_RESULTS = {
    "args": KEEP,  # filtered by ARGS_TEXT before the spec applies
    "elapsed_time": KEEP,
    "metadata": DBT_METADATA,
    "results": Items(RUN_RESULT),
}

RUN_FACETS = {
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
    "dbt_node": _facet(
        alias=KEEP, database=KEEP, original_file_path=KEEP, schema=KEEP, unique_id=KEEP
    ),
    "documentation": _facet(description=TEXT),
    "jobType": _facet(integration=KEEP, jobType=KEEP, processingType=KEEP),
    "sql": _facet(dialect=KEEP, query=SQL),
}

DATASET = {
    "facets": {
        "columnLineage": _facet(dataset=KEEP, fields=KEEP),
        "dataSource": _facet(name=KEEP, uri=KEEP),
        "dbt_model": _facet(
            config=DBT_CONFIG | {"incremental": {"strategy": KEEP, "on_schema_change": KEEP}}
        ),
        "documentation": _facet(description=TEXT),
        "schema": _facet(fields=Items({"name": KEEP, "type": KEEP, "description": TEXT})),
        "symlinks": _facet(identifiers=KEEP),
    },
    "inputFacets": {},  # none reviewed yet: every input facet is dropped
    "name": KEEP,
    "namespace": KEEP,
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
