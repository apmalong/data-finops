"""The Deployment's Pulumi program, as `__main__.py` runs it (spec 08)."""

import os
from dataclasses import dataclass

import pulumi

from deployment.settings import Settings
from deployment.storage import Storage

NAME = "data-finops"


def require_local_state() -> None:
    """Refuse any state backend but a local file (ADR 0001): no hosted control plane, and no
    state anywhere the owner did not choose. The message never quotes the URL."""
    if not os.environ.get("PULUMI_BACKEND_URL", "").startswith("file://"):
        raise RuntimeError(
            "set PULUMI_BACKEND_URL to a file:// backend under .local/ (see the deploy how-to)"
        )


@dataclass(frozen=True)
class Deployment:
    storage: Storage


def build(settings: Settings) -> Deployment:
    """Declare every resource of the Deployment."""
    return Deployment(storage=Storage(NAME, settings))


def main() -> None:
    require_local_state()
    deployment = build(Settings.from_config(pulumi.Config(NAME), pulumi.Config("aws")))
    pulumi.export("bucket", deployment.storage.bucket.bucket)
