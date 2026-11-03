# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `schemas.identity`, ADR 0098
"""The former name of `choregos_api.schemas.identity`: every name still resolves."""

from __future__ import annotations

from choregos_core.compat import replace_module

from . import identity as _current
from .identity import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenDto,
    IntegrationsDto,
    MeDto,
    MembershipDto,
    MembershipUpsert,
    OAuthClientDto,
    OAuthDto,
    OrgCreate,
    OrgDto,
    OrgUpdate,
    TokenScope,
)

__all__ = [
    "ApiTokenCreate",
    "ApiTokenCreated",
    "ApiTokenDto",
    "IntegrationsDto",
    "MeDto",
    "MembershipDto",
    "MembershipUpsert",
    "OAuthClientDto",
    "OAuthDto",
    "OrgCreate",
    "OrgDto",
    "OrgUpdate",
    "TokenScope",
]

replace_module(__name__, _current)
