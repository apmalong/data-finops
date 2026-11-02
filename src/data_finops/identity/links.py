"""Identity Links from explicit Evidence only (ADR 0006, ADR 0022, spec 03 step D).

Pure rules over what the workload store reports: no I/O, no clock, no inference from time,
workgroup or order. Each attempt yields a Link Outcome; only `linked` carries a link.
"""

import json
from collections.abc import Collection

from data_finops.contracts.workload import IdentityLink, LinkOutcome, LinkResolution

POLICY_VERSION = "identity-links/1"

MODEL_EXECUTION = "model_execution"
WORKLOAD_RUN = "workload_run"
DBT_OL_FACETS = "dbt_ol_facets"

ModelExecutionKey = tuple[str, str]
"""A Model Execution's identity: (invocation ID, node ID)."""


def model_execution_id(key: ModelExecutionKey) -> str:
    """A Model Execution as an Identity Link target: `["<invocation>","<node>"]`."""
    return json.dumps(list(key), separators=(",", ":"))


def resolve_model_run(
    run_id: str,
    claims: Collection[ModelExecutionKey],
    reported: Collection[ModelExecutionKey],
) -> LinkResolution:
    """Link a dbt-ol model run to the Model Execution its facets name.

    `claims` are the (invocation, node) pairs the run's observations name through
    `dbt_run.invocation_id` and `dbt_node.unique_id`; `reported` are the Model Executions run
    results reported. More than one claim is ambiguous; a claim nobody reported is unmatched.
    """
    if not claims:
        return LinkResolution(outcome=LinkOutcome.NO_EVIDENCE)
    if len(set(claims)) > 1:
        return LinkResolution(outcome=LinkOutcome.AMBIGUOUS)
    (claim,) = set(claims)
    if claim not in reported:
        return LinkResolution(outcome=LinkOutcome.UNMATCHED)
    invocation, node = claim
    evidence = {"dbt_node.unique_id": node, "dbt_run.invocation_id": invocation}
    return LinkResolution(
        outcome=LinkOutcome.LINKED,
        link=IdentityLink(
            source_type=WORKLOAD_RUN,
            source_id=run_id,
            target_type=MODEL_EXECUTION,
            target_id=model_execution_id(claim),
            evidence_type=DBT_OL_FACETS,
            evidence_value=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            policy_version=POLICY_VERSION,
        ),
    )
