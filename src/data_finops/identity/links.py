"""Identity Links from explicit Evidence only (ADR 0006, ADR 0022, spec 03 step D).

Pure rules over what the workload store reports: no I/O, no clock, no inference from time,
workgroup or order. Each attempt yields a Link Outcome; only `linked` carries a link.
"""

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import NamedTuple

from data_finops.contracts.workload import IdentityLink, LinkOutcome, LinkResolution

POLICY_VERSION = "identity-links/1"


class Identity(StrEnum):
    """The workload identities an Identity Link connects (its source and target types)."""

    MODEL_EXECUTION = "model_execution"
    WORKLOAD_RUN = "workload_run"


class EvidenceType(StrEnum):
    DBT_OL_FACETS = "dbt_ol_facets"
    """`dbt_node.unique_id` (job facet) with `dbt_run.invocation_id` (run facet), ADR 0022."""


class ModelExecutionKey(NamedTuple):
    """A Model Execution's identity: a dbt node within one invocation."""

    invocation_id: str
    node_id: str

    def target_id(self) -> str:
        """As an Identity Link target: `["<invocation>","<node>"]` (spec 02)."""
        return json.dumps([self.invocation_id, self.node_id], separators=(",", ":"))


def resolve_model_run(
    run_id: str,
    claims: set[ModelExecutionKey],
    reported: Mapping[ModelExecutionKey, int],
) -> LinkResolution:
    """Link a dbt-ol model run to the Model Execution its facets name.

    `claims` are the (invocation, node) pairs the run's observations name through
    `dbt_run.invocation_id` and `dbt_node.unique_id`; `reported` counts, per Model Execution,
    the distinct versions run results reported of it. More than one claim, or a Model
    Execution reported in disagreeing versions, is ambiguous (spec 03 step D); a claim nobody
    reported is unmatched.
    """
    if not claims:
        return LinkResolution(outcome=LinkOutcome.NO_EVIDENCE)
    if len(claims) > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    (claim,) = claims
    versions = reported.get(claim, 0)
    if versions == 0:
        return LinkResolution(outcome=LinkOutcome.UNMATCHED)
    if versions > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    evidence = {"dbt_node.unique_id": claim.node_id, "dbt_run.invocation_id": claim.invocation_id}
    return LinkResolution(
        outcome=LinkOutcome.LINKED,
        link=IdentityLink(
            source_type=Identity.WORKLOAD_RUN,
            source_id=run_id,
            target_type=Identity.MODEL_EXECUTION,
            target_id=claim.target_id(),
            evidence_type=EvidenceType.DBT_OL_FACETS,
            evidence_value=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            policy_version=POLICY_VERSION,
        ),
    )
