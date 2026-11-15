"""The Deployment's Pulumi program, as `__main__.py` runs it (spec 08)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pulumi
import pulumi_aws as aws

from deployment.compute import Compute
from deployment.demo import PublicDemo
from deployment.package import DEFAULT_PACKAGE
from deployment.settings import Settings
from deployment.storage import Storage

NAME = "data-finops"
OUTPUTS: dict[str, str] = {
    "bucket": "The Deployment's S3 bucket: the database, the Inboxes and the Lambda package.",
    "apiUrl": "The read-only API's Function URL. Requests must be signed with AWS SigV4 "
    "by a principal with the reader policy.",
    "producerPolicyArn": "The managed policy that lets a producer write to the Inboxes and "
    "nothing else. Attach it to whatever sends OpenLineage events or dbt artifacts.",
    "readerPolicyArn": "The managed policy that lets a principal call the API's Function URL.",
    "collectorFunction": "The collector function's name, for its logs or to invoke a "
    "Collection Run by hand.",
    "apiFunction": "The API function's name, for its logs.",
    "demoBucket": "Only with publicDemo: the public demo's bucket, with the demo dataset.",
    "demoGeneratorFunction": "Only with publicDemo: the function that writes the demo "
    "dataset. Invoke it one time after the first deploy.",
}
"""Each stack output and what it is for. The site's generated deployment settings reference
reads this literal from the source, and `Deployment.outputs` gives exactly these, less
`DEMO_OUTPUTS` when `publicDemo` is off."""
DEMO_OUTPUTS = ("demoBucket", "demoGeneratorFunction")


def require_local_state() -> None:
    """Refuse any state backend but a local file (ADR 0001): no hosted control plane, and no
    state anywhere the owner did not choose. The message never quotes the URL."""
    if not os.environ.get("PULUMI_BACKEND_URL", "").startswith("file://"):
        raise RuntimeError(
            "set PULUMI_BACKEND_URL to a file:// backend under .local/ (see the deploy how-to)"
        )


def require_package(package: Path) -> None:
    """Refuse to deploy functions without their package, built first (spec 08, #186)."""
    if not package.is_file():
        raise RuntimeError(
            "build the Lambda package first: python -m deployment.package (in infra/pulumi/aws)"
        )


@dataclass(frozen=True)
class Deployment:
    storage: Storage
    compute: Compute
    demo: PublicDemo | None = None

    @property
    def ready(self) -> pulumi.Output[list[Any]]:
        """Settles once every resource of every component is registered."""
        components = [self.storage.ready, self.compute.ready]
        if self.demo is not None:
            components.append(self.demo.ready)
        return pulumi.Output.all(*components)

    def outputs(self) -> dict[str, pulumi.Output[str]]:
        """The stack's outputs (spec 08 §Settings, Outputs; spec 09 §Stack)."""
        demo = {} if self.demo is None else self.demo.outputs()
        return {"bucket": self.storage.bucket.bucket, **self.compute.outputs(), **demo}


def build(
    settings: Settings,
    *,
    package: Path = DEFAULT_PACKAGE,
    account_id: pulumi.Input[str] | None = None,
) -> Deployment:
    """Declare every resource of the Deployment, in the account the deployer is in unless
    `account_id` says otherwise."""
    if account_id is None:
        account_id = aws.get_caller_identity_output().account_id
    storage = Storage(NAME, settings)
    compute = Compute(NAME, settings, storage, package=package, account_id=account_id)
    demo = PublicDemo(NAME, settings, code=compute.code) if settings.public_demo else None
    return Deployment(storage=storage, compute=compute, demo=demo)


def main() -> None:
    require_local_state()
    require_package(DEFAULT_PACKAGE)
    deployment = build(Settings.from_config(pulumi.Config(NAME), pulumi.Config("aws")))
    for name, value in deployment.outputs().items():
        pulumi.export(name, value)
