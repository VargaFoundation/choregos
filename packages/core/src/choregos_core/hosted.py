# SPDX-License-Identifier: Apache-2.0
"""What a hosted agent's container sees of the platform (ADR 0080): three loopback addresses and
nothing that opens anything.

The container runs beside the runner in the run's pod and shares its network namespace. The runner
listens on `127.0.0.1`: the model on 4001, the MCP servers on 4002, an HTTP CONNECT proxy on 4003.
The container's environment points every SDK there, with a key that only the loopback accepts:
the run's gateway key, its run token and the MCP servers' credentials stay in the runner.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from choregos_contracts import HostedCall, StageInput

from .domain import AgentContainer

LOOPBACK_HOST = "127.0.0.1"
MODEL_PORT = 4001
MCP_PORT = 4002
CONNECT_PORT = 4003
#: The key the container's SDKs send; the model proxy accepts it and nothing else does.
LOOPBACK_KEY = "choregos-loopback"
#: The oldest Kubernetes that runs the container as a native sidecar (an init container with
#: `restartPolicy: Always`, beta and on by default from 1.29). An older API server drops the field
#: and runs the container as a plain init container: the pod never gets past it (S41-16).
NATIVE_SIDECARS_MIN = (1, 29)
#: The error a hosted version or start gets on a cluster older than `NATIVE_SIDECARS_MIN`.
UNSUPPORTED_CLUSTER = "hosted_unsupported_cluster"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)")
_NUMBER = re.compile(r"^(\d+)")


def kubernetes_version(version: str | Mapping[str, Any] | None) -> tuple[int, int] | None:
    """`(major, minor)` of a Kubernetes version, `None` when it cannot be read.

    Takes a `gitVersion` (`v1.29.3-eks-abc`, `1.30+`), or the body of the API server's `/version`,
    whose `gitVersion` is read first and its `major`/`minor` (`"1"`, `"30+"` on GKE) after."""
    if isinstance(version, Mapping):
        parsed = kubernetes_version(str(version.get("gitVersion") or ""))
        if parsed is not None:
            return parsed
        major = _NUMBER.match(str(version.get("major") or ""))
        minor = _NUMBER.match(str(version.get("minor") or ""))
        return (int(major[1]), int(minor[1])) if major and minor else None
    matched = _VERSION.match((version or "").strip())
    return (int(matched[1]), int(matched[2])) if matched else None


def native_sidecars_refusal(version: str | Mapping[str, Any] | None) -> str | None:
    """Why a hosted container cannot run on a cluster of this version; `None` when it can."""
    parsed = kubernetes_version(version)
    shown = version.get("gitVersion") if isinstance(version, Mapping) else version
    wanted = ".".join(map(str, NATIVE_SIDECARS_MIN))
    if parsed is None:
        return f"{UNSUPPORTED_CLUSTER}: the cluster's Kubernetes version `{shown}` cannot be read"
    if parsed < NATIVE_SIDECARS_MIN:
        return (
            f"{UNSUPPORTED_CLUSTER}: a hosted agent runs as a native sidecar, which needs Kubernetes "
            f"{wanted} or later; this cluster runs {parsed[0]}.{parsed[1]}"
        )
    return None


def loopback_url(port: int) -> str:
    return f"http://{LOOPBACK_HOST}:{port}"


def sidecar_env(
    hosted: HostedCall,
    *,
    run_id: str,
    model: str,
    mcp_servers: Iterable[str] = (),
    ports: tuple[int, int, int] = (MODEL_PORT, MCP_PORT, CONNECT_PORT),
) -> dict[str, str]:
    """The hosted container's environment: the version's own variables, then the platform's, which
    win. The version's variables cannot name a reserved one: publication refused them (S41-01).
    `ports`: the model, MCP and CONNECT listeners, the fixed ones but in tests."""
    servers = sorted(mcp_servers)
    model_port, mcp_port, connect_port = ports
    proxy = loopback_url(connect_port)
    return {
        **hosted.env,
        "PORT": str(hosted.port),
        "CHOREGOS_RUN_ID": run_id,
        "CHOREGOS_AGENT_SLUG": hosted.agent_slug,
        "CHOREGOS_AGENT_VERSION": str(hosted.agent_version),
        "CHOREGOS_MODEL": model,
        # One streamable-HTTP endpoint per server: `<CHOREGOS_MCP_URL><name>`.
        "CHOREGOS_MCP_URL": f"{loopback_url(mcp_port)}/mcp/",
        "CHOREGOS_MCP_SERVERS": ",".join(servers),
        "OPENAI_BASE_URL": f"{loopback_url(model_port)}/v1",
        "OPENAI_API_KEY": LOOPBACK_KEY,
        "ANTHROPIC_BASE_URL": loopback_url(model_port),
        "ANTHROPIC_API_KEY": LOOPBACK_KEY,
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": f"{LOOPBACK_HOST},localhost",
        "no_proxy": f"{LOOPBACK_HOST},localhost",
    }


def agent_container(
    stage_input: StageInput,
    *,
    cpu: str = "1",
    memory: str = "2Gi",
    mcp_servers: Iterable[str] | None = None,
) -> AgentContainer | None:
    """The container an executor starts beside the runner for a hosted step; `None` for any other.

    Only the MCP servers the runner's proxy serves are announced: the granted ones with a URL, or
    `mcp_servers` when the runner serves others (a session's pod serves `choregos`, S41-11)."""
    hosted = stage_input.hosted
    if hosted is None:
        return None
    servers = (
        list(mcp_servers)
        if mcp_servers is not None
        else [name for name, ref in stage_input.tools.mcp.items() if ref.url]
    )
    return AgentContainer(
        image=hosted.image,
        env=sidecar_env(
            hosted, run_id=stage_input.run_id, model=stage_input.model.litellm_model, mcp_servers=servers
        ),
        port=hosted.port,
        termination_grace_s=hosted.termination_grace_s,
        cpu=cpu,
        memory=memory,
    )
