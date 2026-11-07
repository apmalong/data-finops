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
        if args.typ == "aws:lambda/function:Function":
            outputs["arn"] = f"arn:aws:lambda:us-east-1:123456789012:function:{args.inputs['name']}"
        if args.typ == "aws:lambda/functionUrl:FunctionUrl":
            outputs["functionUrl"] = "https://mock.lambda-url.us-east-1.on.aws/"
        outputs.setdefault("arn", f"arn:mock:{args.typ}:{args.name}")
        return f"{args.name}-id", outputs

    def call(
        self, args: pulumi.runtime.MockCallArgs
    ) -> tuple[dict[Any, Any], list[tuple[str, str]] | None]:
        """Function calls: the caller's identity is the only one the stack makes. Failures are
        an empty list, never None, which the SDK iterates."""
        if args.token == "aws:index/getCallerIdentity:getCallerIdentity":
            return {
                "accountId": "123456789012",
                "arn": "arn:aws:iam::123456789012:user/deployer",
                "id": "123456789012",
                "userId": "AIDAEXAMPLE",
            }, []
        return {}, []

    def of_type(self, typ: str) -> list[pulumi.runtime.MockResourceArgs]:
        return [r for r in self.resources if r.typ == typ]

    def one(self, typ: str) -> pulumi.runtime.MockResourceArgs:
        (resource,) = self.of_type(typ)
        return resource


MOCKS = RecordingMocks()
pulumi.runtime.set_mocks(MOCKS, project="data-finops", stack="test", preview=False)
