"""The API Gateway HTTP API of the Deployment, with IAM routes, in place of the Function URL
(spec 10 §The API; ADR 0032)."""

import json
from dataclasses import dataclass

import pulumi
import pulumi_aws as aws

from deployment.settings import Settings

READ_ROUTES: dict[str, str] = {
    "summary": "/v1/costs/summary",
    "allocations": "/v1/allocations",
    "explanation": "/v1/allocations/{ref}/explanation",
    "rollups": "/v1/rollups",
    "dimension-rollups": "/v1/rollups/dimensions/{name}",
    "reconciliation": "/v1/reconciliation",
    "provenance": "/v1/provenance",
    "lineage": "/v1/lineage",
}
"""Each read route of the app (`data_finops.api.app`, spec 06), by a short name for its
resource. Only these paths reach the API function: `/health` and the OpenAPI document have no
route."""
ROLES = ("reader", "consumer", "operator")
"""The Control Roles (spec 10), each with a managed policy. Until the Command routes come (#291),
each role may invoke the read routes only."""


@dataclass(frozen=True)
class Gateway:
    url: pulumi.Output[str]
    """The API's address, with a trailing `/`."""
    policies: dict[str, aws.iam.Policy]
    """The managed policy of each role, by role."""
    resources: list[pulumi.Resource]


def build_gateway(
    name: str, settings: Settings, function: aws.lambda_.Function, child: pulumi.ResourceOptions
) -> Gateway:
    """The HTTP API in front of `function`: a route for each read with IAM authorization, a
    `$default` stage with the rate limits, and a policy for each Control Role."""
    stack = pulumi.get_stack()
    api = aws.apigatewayv2.Api(
        f"{name}-api", name=f"{name}-{stack}-api", protocol_type="HTTP", opts=child
    )
    integration = aws.apigatewayv2.Integration(
        f"{name}-api",
        api_id=api.id,
        integration_type="AWS_PROXY",
        integration_uri=function.arn,
        payload_format_version="2.0",
        opts=child,
    )
    routes = [
        aws.apigatewayv2.Route(
            f"{name}-{short_name}",
            api_id=api.id,
            route_key=f"GET {path}",
            authorization_type="AWS_IAM",
            target=integration.id.apply(lambda id: f"integrations/{id}"),
            opts=child,
        )
        for short_name, path in READ_ROUTES.items()
    ]
    stage = aws.apigatewayv2.Stage(
        f"{name}-api",
        api_id=api.id,
        name="$default",
        auto_deploy=True,
        default_route_settings={
            "throttling_rate_limit": settings.api_rate_limit,
            "throttling_burst_limit": settings.api_burst_limit,
        },
        opts=child,
    )
    permission = aws.lambda_.Permission(
        f"{name}-api",
        action="lambda:InvokeFunction",
        function=function.name,
        principal="apigateway.amazonaws.com",
        source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/GET/v1/*"),
        opts=child,
    )
    policies = {
        role: aws.iam.Policy(
            f"{name}-{role}",
            description=f"data-finops: call the Deployment's API as a {role}",
            policy=api.execution_arn.apply(_invoke_policy),
            opts=child,
        )
        for role in ROLES
    }
    return Gateway(
        url=api.api_endpoint.apply(lambda endpoint: f"{endpoint}/"),
        policies=policies,
        resources=[api, integration, *routes, stage, permission, *policies.values()],
    )


def route_arns(execution_arn: str) -> list[str]:
    """The ARN of each read route, as `execute-api:Invoke` names it. A path parameter is `*`.
    The stage is `*`: the API has one stage."""
    return sorted(
        f"{execution_arn}/*/GET" + "/".join("*" if part.startswith("{") else part for part in parts)
        for parts in (path.split("/") for path in READ_ROUTES.values())
    )


def _invoke_policy(execution_arn: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "execute-api:Invoke",
                    "Resource": route_arns(execution_arn),
                }
            ],
        }
    )
