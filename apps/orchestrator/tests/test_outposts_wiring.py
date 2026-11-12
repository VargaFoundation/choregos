# SPDX-License-Identifier: Apache-2.0
"""The workers register the platform's factory for a connector at an outpost (S65-13, ADR 0149).

A governed action's effect is played by the orchestrator. When it calls a connector of type
`mcp-outpost`, the client is built over the API's rendezvous, as in the API: without the
registration, the adapters' own factory would build nothing and every such effect would be refused.
"""

from __future__ import annotations

import subprocess
import sys


def test_the_activities_register_the_factory_that_supplies_the_rendezvous() -> None:
    """A process of its own, as a worker is: what another test registered is not taken for it."""
    probe = (
        "from choregos_adapters import registry, builtins\n"
        "assert registry._REGISTRY[('mcp', 'mcp-outpost')] is builtins._mcp_outpost_client\n"
        "import choregos_orchestrator.activities.base\n"
        "from choregos_api import adapter_wiring\n"
        "assert registry._REGISTRY[('mcp', 'mcp-outpost')] is adapter_wiring._outpost_tools\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
