"""Identity Links from explicit Evidence only (ADR 0006, ADR 0022, spec 03 step D).

Pure rules over what the workload store reports: no I/O, no clock, no inference from time,
workgroup or order. Each attempt yields a Link Outcome; only `linked` carries a link.
"""

import json
from collections.abc import Callable, Collection, Mapping
from enum import StrEnum
from typing import Any, NamedTuple, TypeGuard

from data_finops.contracts.workload import IdentityLink, LinkOutcome, LinkResolution

POLICY_VERSION = "identity-links/1"


class Identity(StrEnum):
    """The workload identities an Identity Link connects (its source and target types)."""

    DBT_INVOCATION = "dbt_invocation"
    MODEL_EXECUTION = "model_execution"
    QUERY_EXECUTION = "query_execution"
    WORKLOAD_RUN = "workload_run"


class EvidenceType(StrEnum):
    DBT_OL_FACETS = "dbt_ol_facets"
    """`dbt_node.unique_id` (job facet) with `dbt_run.invocation_id` (run facet), ADR 0022."""
    DBT_RUN_FACET = "dbt_run_facet"
    """`dbt_run.invocation_id` on a run without a `dbt_node` job facet: the invocation's run."""
    TRUSTED_QUERY_COMMENT = "trusted_query_comment"
    """`invocation_id` and `node_id` from the dbt comment that starts the query text."""


class ModelExecutionKey(NamedTuple):
    """A Model Execution's identity: a dbt node within one invocation."""

    invocation_id: str
    node_id: str

    def target_id(self) -> str:
        """As an Identity Link target: `["<invocation>","<node>"]` (spec 02)."""
        return json.dumps([self.invocation_id, self.node_id], separators=(",", ":"))


class QueryExecutionKey(NamedTuple):
    """A Query Execution's identity: the provider's ID within an account and region."""

    account_id: str
    region: str
    query_execution_id: str

    def source_id(self) -> str:
        """As an Identity Link source: `["<account>","<region>","<id>"]` (spec 02)."""
        return json.dumps(list(self), separators=(",", ":"))


def resolve_model_run(
    run_id: str,
    claims: set[ModelExecutionKey],
    reported: Mapping[ModelExecutionKey, int],
) -> LinkResolution:
    """Link a dbt-ol model run to the Model Execution its facets name (ADR 0022).

    `claims` pair every `dbt_run.invocation_id` with every `dbt_node.unique_id` the run's
    observations carry, so the two may come from different events of the run. See `_resolve`.
    """
    return _resolve(
        Identity.WORKLOAD_RUN,
        run_id,
        EvidenceType.DBT_OL_FACETS,
        claims,
        reported,
        lambda claim: {
            "dbt_node.unique_id": claim.node_id,
            "dbt_run.invocation_id": claim.invocation_id,
        },
    )


def resolve_query(
    query: QueryExecutionKey,
    claims: set[ModelExecutionKey | None],
    reported: Mapping[ModelExecutionKey, int],
) -> LinkResolution:
    """Link a Query Execution to the Model Execution its Trusted Query Comment names.

    `claims` holds what each stored observation of the query names, None for an observation
    without a Trusted Query Comment; observations that disagree, with or without a comment,
    are ambiguous. See `_resolve` for the rest.
    """
    return _resolve(
        Identity.QUERY_EXECUTION,
        query.source_id(),
        EvidenceType.TRUSTED_QUERY_COMMENT,
        claims,
        reported,
        lambda claim: {"invocation_id": claim.invocation_id, "node_id": claim.node_id},
    )


def resolve_invocation(invocation_id: str, reporters: Mapping[str, bool]) -> LinkResolution:
    """Link a dbt invocation to the Workload Run that reported it (spec 03 step D).

    `reporters` maps each run whose events carry `dbt_run.invocation_id` for this invocation
    to whether any of its events also carries a `dbt_node` job facet. Those are dbt-ol's
    per-model runs, not the invocation's own run. No such run is unmatched (for example
    `dbt-ol seed`, which emits no `dbt_run` facet); several are ambiguous.
    """
    runs = {run_id for run_id, is_model_run in reporters.items() if not is_model_run}
    if not runs:
        return LinkResolution(outcome=LinkOutcome.UNMATCHED)
    if len(runs) > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    (run_id,) = runs
    return LinkResolution(
        outcome=LinkOutcome.LINKED,
        link=IdentityLink(
            source_type=Identity.DBT_INVOCATION,
            source_id=invocation_id,
            target_type=Identity.WORKLOAD_RUN,
            target_id=run_id,
            evidence_type=EvidenceType.DBT_RUN_FACET,
            evidence_value=_compact({"dbt_run.invocation_id": invocation_id}),
            policy_version=POLICY_VERSION,
        ),
    )


def trusted_query_comment(query: str) -> ModelExecutionKey | None:
    """The Model Execution a Trusted Query Comment names, or None (GLOSSARY, spec 03 step D).

    Only the first line, and at most MAX_COMMENT_CHARS of it, is read. It must start with
    `-- /* ` (dbt-athena) or `/* `, followed by a JSON object and ` */`, with `"app": "dbt"`
    and non-blank string `node_id` and `invocation_id`. The object may not contain `*/`
    (where SQL would end the comment) or repeat a key. Anything else in the comment is
    ignored; a comment anywhere else in the query is not Evidence, so SQL cannot forge one
    mid-statement.
    """
    line = query[:MAX_COMMENT_CHARS].partition("\n")[0]
    prefix = next((p for p in _COMMENT_PREFIXES if line.startswith(p)), None)
    if prefix is None:
        return None
    try:
        document, end = _DECODER.raw_decode(line, len(prefix))
    except ValueError:
        return None
    if not line.startswith(" */", end) or "*/" in line[len(prefix) : end]:
        return None
    if not isinstance(document, dict):
        return None
    node, invocation = document.get("node_id"), document.get("invocation_id")
    if document.get("app") != "dbt" or not _identifier(node) or not _identifier(invocation):
        return None
    return ModelExecutionKey(invocation_id=invocation, node_id=node)


MAX_COMMENT_CHARS = 4096
"""How much of a query's text is ever read for its comment; dbt's comment is far shorter."""

_COMMENT_PREFIXES = ("-- /* ", "/* ")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("repeated key")
    return dict(pairs)


_DECODER = json.JSONDecoder(object_pairs_hook=_unique_keys)


def _compact(document: dict[str, str]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def is_identifier(value: object) -> TypeGuard[str]:
    """A string with something other than whitespace: the least an identifier in Evidence is."""
    return _identifier(value)


def _identifier(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _resolve(
    source_type: Identity,
    source_id: str,
    evidence_type: EvidenceType,
    claims: Collection[ModelExecutionKey | None],
    reported: Mapping[ModelExecutionKey, int],
    evidence: Callable[[ModelExecutionKey], dict[str, str]],
) -> LinkResolution:
    """The shared rule over what each observation of the source names (spec 03 step D).

    A None claim is an observation naming nothing. No named Model Execution is no evidence;
    observations that disagree (including one naming nothing beside one naming something),
    or a Model Execution reported in disagreeing versions, are ambiguous; a Model Execution
    nobody reported is unmatched. `reported` counts, per Model Execution, the distinct
    versions run results reported of it.
    """
    named = {claim for claim in claims if claim is not None}
    if not named:
        return LinkResolution(outcome=LinkOutcome.NO_EVIDENCE)
    if len(set(claims)) > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    (claim,) = named
    versions = reported.get(claim, 0)
    if versions == 0:
        return LinkResolution(outcome=LinkOutcome.UNMATCHED)
    if versions > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    return LinkResolution(
        outcome=LinkOutcome.LINKED,
        link=IdentityLink(
            source_type=source_type,
            source_id=source_id,
            target_type=Identity.MODEL_EXECUTION,
            target_id=claim.target_id(),
            evidence_type=evidence_type,
            evidence_value=_compact(evidence(claim)),
            policy_version=POLICY_VERSION,
        ),
    )
