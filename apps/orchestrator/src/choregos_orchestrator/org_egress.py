# SPDX-License-Identifier: Apache-2.0
"""The hosts an organisation opens for its runners (S39-22d, ADR 0066).

A rule of the organisation's egress allowlist whose `used_by` contains `runners` joins the project
policy's `allow_domains` for every run of the organisation: the runner's own guard, the egress
proxy the provisioning renders and (with remote agents) the admission check all read the union. An
administrator's "allow `a2a.example.test` for the remote agent" therefore works in every project
without editing each policy. Read in activities only: nothing under `workflows/` changes.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Any

from choregos_api.db.models import OrgEgressRule
from sqlalchemy import select


def runner_domain(host: str) -> str | None:
    """The domain a runner's guard and the proxy's `dstdomain` understand: a name covers itself and
    its subdomains, so `*.x.test` becomes `x.test`. An IP literal is not a domain."""
    bare = host.removeprefix("*.")
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        return bare or None
    return None


def merged_domains(project: Iterable[str], organisation: Iterable[str]) -> list[str]:
    """The project's own allowlist, then what the organisation opened, sorted and without doubles."""
    return sorted({*project, *organisation})


async def org_runner_hosts(session: Any, org_id: str) -> list[str]:
    """The domains of the organisation's `allowed` rules that name `runners`."""
    rows = (
        await session.execute(
            select(OrgEgressRule.host, OrgEgressRule.used_by).where(
                OrgEgressRule.org_id == org_id, OrgEgressRule.status == "allowed"
            )
        )
    ).tuples()
    domains = (runner_domain(host) for host, used_by in rows if "runners" in (used_by or []))
    return sorted({d for d in domains if d})
