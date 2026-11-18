# SPDX-License-Identifier: Apache-2.0
"""Who may administer the organisation's connectors, shared by the connector routers (ADR 0099)."""

from __future__ import annotations

from ..errors import forbidden
from ..rbac import Permission, Principal


def require_connector_admin(principal: Principal, org: str) -> None:
    """Declare a connector, open an operation, choose its groups: deciding what the organisation
    opens to its projects. The organisation's administrator only."""
    if not principal.can(Permission.TOOLS_GRANT, org):
        raise forbidden(f"the connectors of {org} and their operations are decided by its administrator")
