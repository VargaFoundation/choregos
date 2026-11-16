# SPDX-License-Identifier: Apache-2.0
"""Choregos adapters: everything that talks to the outside world.

An adapter implements a `Protocol` of `base.py` and **has no business logic**: the decision is in
`packages/core`, the orchestration in `apps/orchestrator`.

This root keeps the public API. The connector type specs are in `specs.py`, the factories and the
builtin registrations in `builtins.py`; the mutable fakes of `CHOREGOS_FAKES=1` live here, because
tests rebind them as `choregos_adapters.FAKE_*` and the factories read them from this package.
"""

from __future__ import annotations

from typing import Any

from .base import (
    AgentBackend,
    CdAdapter,
    CiAdapter,
    DecisionClient,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)
from .builtins import (  # noqa: F401 - names the root has always exposed
    _github_client,
    _jira_tracker,
    _register_builtins,
    fake_mcp_gateway,
    fake_outpost,
)
from .registry import (
    POLICIES,
    AdapterSet,
    ConnectorTypeSpec,
    OperationSpec,
    account_of,
    available,
    build,
    connector_types,
    default_type,
    deployment_default_type,
    egress_hosts,
    fakes_enabled,
    offered_connector_types,
    operation_spec,
    register,
    resolved_configuration,
    spec_of,
    validate_config,
)
from .specs import (  # noqa: F401 - root names
    _SHIPPED_SPECS,
    GATEWAY_SERVER_PATTERN,
    MCP_GATEWAY_TYPE,
    MCP_OUTPOST_TYPE,
)

__version__ = "0.1.0"

__all__ = [
    "POLICIES",
    "AdapterSet",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "ConnectorTypeSpec",
    "DecisionClient",
    "Executor",
    "GatewayAdapter",
    "MemoryAdapter",
    "Notifier",
    "OperationSpec",
    "ScmAdapter",
    "TrackerAdapter",
    "account_of",
    "available",
    "build",
    "connector_types",
    "default_type",
    "deployment_default_type",
    "egress_hosts",
    "fake_mcp_gateway",
    "fake_outpost",
    "fakes_enabled",
    "load_extensions",
    "offered_connector_types",
    "operation_spec",
    "register",
    "resolved_configuration",
    "spec_of",
    "validate_config",
]

# The process-wide fakes of CHOREGOS_FAKES=1, scripted by the tests and the demo. `None` until the
# first build; the factories in `builtins.py` create them here, and a test rebinds them here.
FAKE_MCP: Any = None  # the fake MCP server
FAKE_IDP: Any = None  # the fake identity provider (ADR 0135), served under CHOREGOS_FAKES=1
FAKE_OAUTH: Any = None  # the fake OAuth provider of a `user_consent` source (ADR 0135)
FAKE_MCP_GATEWAY: Any = None  # the fake MCP gateway, whose fallback is always the CURRENT `FAKE_MCP`
FAKE_OUTPOST: Any = None  # the fake outpost (ADR 0149), whose every alias is served by the CURRENT `FAKE_MCP`
FAKE_ENTRA: Any = None  # the fake Graph
FAKE_MARKETDATA: Any = None  # the fake of the `marketdata` family
FAKE_WEB_SEARCH: Any = None  # the fake of the `web_search` family
FAKE_TEAMS: Any = None  # the fake Teams webhook (S63-25)
#: The netops fakes (ADR 0052): one pfSense, one cluster, one recorded Hubble trace, one GitOps repository.
FAKE_NETOPS: dict[str, Any] = {}
#: The fakes of the business families, one per family.
FAUX_METIER: dict[str, Any] = {}  # lang: fr-ok (not renamed by S45-10: the business fakes keep their name)

#: The entry-point group the platform reads at startup. A package installed alongside (the
#: enterprise edition, an in-house connector) declares itself there and registers itself.
EXTENSION_GROUP = "choregos.plugins"


def load_extensions(group: str = EXTENSION_GROUP) -> list[str]:
    """Loads the extensions declared outside this tree, and returns their names.

    Until 2026-09-26 there was NO seam: `register()` was public, but nothing ever imported a
    third-party module, so an outside package could not register itself unless
    `_register_builtins()` was patched. The playbooks were the only capability truly extensible
    from the outside (`CHOREGOS_PLAYBOOKS_DIR`); that is the model copied here.

    Each entry point designates a callable with no argument, which makes its `register(...)` calls.

    **A declared extension that does not load stops the process.** The same rule as `guard.yaml`
    and as the refusal of an unresolved setting: someone installed it so that it would serve, and
    a silently absent extension leaves a platform that looks complete and is not.
    """
    from importlib import metadata

    loaded: list[str] = []
    for point in metadata.entry_points(group=group):
        try:
            point.load()()
        except Exception as error:
            raise RuntimeError(
                f'extension "{point.name}" ({point.value}): cannot load: {error}. '
                "It is declared, so it must serve; the platform does not start without it."
            ) from error
        loaded.append(point.name)
    return loaded


_register_builtins()
