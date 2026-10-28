# SPDX-License-Identifier: Apache-2.0
"""The real API behind the console's live journeys (`apps/web/e2e-live/`, S40-17c).

The console's Playwright journeys (`apps/web/e2e/`) run on its mock; a live journey drives the real
API instead, so what the console sends and reads is what the API accepts and answers. This launcher
starts it on fakes (`CHOREGOS_FAKES=1`) with a throwaway SQLite database and one fake MCP server
whose tools an organisation connector discovers as operations. Usage:
`uv run python tools/e2e_live_api.py <port> <console URL>`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

#: The tools of the fake MCP server: one read, one write, as a supplier's server would list them.
TOOLS = [
    {
        "name": "order_status",
        "description": "Where an order stands",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {"number": {"type": "string"}},
            "required": ["number"],
        },
    },
    {"name": "order_laptop", "description": "Orders a laptop", "inputSchema": {"type": "object"}},
]


def main(argv: list[str]) -> None:
    port = int(argv[1]) if len(argv) > 1 else 8765
    console = argv[2] if len(argv) > 2 else "http://127.0.0.1:3100"
    # Where a sign-in lands once done: the console under test, not the default `localhost:3000`.
    os.environ["CHOREGOS_PUBLIC_URL"] = console
    database = Path(tempfile.mkdtemp(prefix="choregos-e2e-live-")) / "choregos.db"
    os.environ["CHOREGOS_ENV"] = "test"
    os.environ["CHOREGOS_FAKES"] = "1"
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    # `?as=<email>` signs in without an identity provider; refused outside dev and test.
    os.environ["CHOREGOS_DEV_LOGIN_ENABLED"] = "true"
    os.environ.setdefault("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    # The organisation a fresh install creates at its first start, and its administrator.
    os.environ.setdefault("CHOREGOS_BOOTSTRAP_ORG", "varga")
    os.environ.setdefault("CHOREGOS_BOOTSTRAP_ORG_NAME", "Varga Foundation")
    os.environ.setdefault("CHOREGOS_BOOTSTRAP_ADMINS", "admin@varga.dev")

    import choregos_adapters
    import uvicorn
    from choregos_adapters.fakes.mcp import FakeMcpServer

    choregos_adapters.FAKE_MCP = FakeMcpServer(outils=[dict(t) for t in TOOLS])
    uvicorn.run("choregos_api.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv)
