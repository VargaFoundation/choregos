# SPDX-License-Identifier: Apache-2.0
"""What the agent of an ACP step is told when it runs in a container of its own (ADR 0146 §7, S65-04f).

The runner serves the three loopback listeners of a hosted step for the agent too: the model, the MCP
servers that carry a credential, and the CONNECT proxy. The agent is handed a COPY of the stage input
that names them: the loopback address in place of the gateway, the constant loopback key in place of the
run's key, the loopback address in place of each credentialed MCP server with no header, and the
loopback CONNECT proxy in place of the pod's egress proxy (whose URL carries the run's egress token).
The real values stay in the runner's own copy, which the listeners forward with.
"""

from __future__ import annotations

from collections.abc import Mapping

from tranibor_contracts import McpServerRef, StageInput
from tranibor_core.hosted import LOOPBACK_HOST, LOOPBACK_KEY, loopback_url

from ..egress import PROXY_VARIABLES


def agent_view(stage_input: StageInput, ports: tuple[int, int, int], *, model: bool = True) -> StageInput:
    """`stage_input` as the agent may see it. `model=False`: the backend cannot speak to the model
    listener, so its model is left as it is (and the run's key with it)."""
    model_port, mcp_port, _ = ports
    update: dict[str, object] = {}
    if model and stage_input.gateway_key:
        update["model"] = stage_input.model.model_copy(update={"base_url": loopback_url(model_port)})
        update["gateway_key"] = LOOPBACK_KEY
    servers = {
        name: McpServerRef(url=f"{loopback_url(mcp_port)}/mcp/{name}") if ref.url and ref.env else ref
        for name, ref in stage_input.tools.mcp.items()
    }
    update["tools"] = stage_input.tools.model_copy(update={"mcp": servers})
    return stage_input.model_copy(update=update)


def proxy_environment(env: Mapping[str, str], ports: tuple[int, int, int]) -> dict[str, str]:
    """The proxy variables the agent (and the project's checks) start with: the loopback CONNECT proxy
    wherever the runner's environment names a proxy, and loopback kept out of it. Empty when the runner
    has no proxy: nothing then carries a credential."""
    if not any(env.get(name) for name in PROXY_VARIABLES):
        return {}
    proxy = loopback_url(ports[2])
    changes = dict.fromkeys(PROXY_VARIABLES, proxy)
    for name in ("NO_PROXY", "no_proxy"):
        existing = env.get(name, "")
        changes[name] = ",".join(part for part in (LOOPBACK_HOST, "localhost", existing) if part)
    return changes
