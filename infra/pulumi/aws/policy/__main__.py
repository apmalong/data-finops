"""The policy pack (spec 08 §Policy pack): run by `pulumi preview --policy-pack` or
`pulumi policy analyze`, never imported by tests."""

from policies import POLICIES
from pulumi_policy import PolicyPack

PolicyPack("data-finops", POLICIES)
