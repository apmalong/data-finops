"""The HTTP API (spec 06): a thin FastAPI adapter over the `reporting` read model.

Needs the optional `api` extra. Every read opens the read model for one request; errors are
RFC 9457 Problem Details that name fields, never input values.
"""

from data_finops.api.app import create_app
from data_finops.api.token import TOKEN_VARIABLE, read_token
from data_finops.reporting import BillingLineDetail

__all__ = ["TOKEN_VARIABLE", "BillingLineDetail", "create_app", "read_token"]
