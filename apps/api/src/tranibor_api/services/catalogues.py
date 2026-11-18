# SPDX-License-Identifier: Apache-2.0
"""What the MCP and skills catalogues share on the API side (ADR 0065)."""

from __future__ import annotations

from typing import Literal

Egress = Literal["open", "undeclared", "unknown"]


def host_open(host: str, declared: list[str]) -> bool:
    """`mcp.notion.com` is open when the deployment declares it, or a `*.notion.com` wildcard."""
    host = host.lower()
    for pattern in (d.lower().strip() for d in declared):
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:])):
            return True
    return False


def egress_of(hosts: list[str], declared: list[str]) -> Egress:
    """Whether the deployment says the API can reach every one of `hosts`.

    `unknown` when the deployment declares nothing (the common case: the network policy is not the
    API's to describe) or there is no host to reach. `undeclared` is a warning, not a verdict: the
    network policy decides, this only tells the administrator before a silent timeout."""
    if not declared or not hosts:
        return "unknown"
    return "open" if all(host_open(h, declared) for h in hosts) else "undeclared"
