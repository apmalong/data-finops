"""Pulumi's unit-test mocks, recording every resource the stack declares (seam 6). Set once,
before any module that declares resources is imported; nothing ever reaches AWS."""

from typing import Any

import pulumi


class RecordingMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.resources.append(args)
        outputs = {**args.inputs}
        if args.typ == "aws:s3/bucket:Bucket":
            outputs |= {
                "bucket": "mock-deployment-bucket",
                "arn": "arn:aws:s3:::mock-deployment-bucket",
            }
        return f"{args.name}-id", outputs

    def call(
        self, args: pulumi.runtime.MockCallArgs
    ) -> tuple[dict[Any, Any], list[tuple[str, str]] | None]:
        return {}, None

    def of_type(self, typ: str) -> list[pulumi.runtime.MockResourceArgs]:
        return [r for r in self.resources if r.typ == typ]

    def one(self, typ: str) -> pulumi.runtime.MockResourceArgs:
        (resource,) = self.of_type(typ)
        return resource


MOCKS = RecordingMocks()
pulumi.runtime.set_mocks(MOCKS, project="data-finops", stack="test", preview=False)
