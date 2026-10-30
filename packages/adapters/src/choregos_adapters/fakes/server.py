# SPDX-License-Identifier: Apache-2.0
"""The fakes of the HR scenario served by ONE process (S20-07): what a deployment needs where the
API and the orchestrator are separate processes — dev.

An in-memory fake keeps its state in the process that built it: the activity that enrols a device
(the orchestrator) and the broker that reads its state back (the API) would see two fleets. Served
here, they see only one. The application is a bare ASGI callable — the adapters package depends on
no framework — that `uvicorn` serves: `python -m choregos_adapters.fakes.server`.

- `/{mdm,shipping,access_control,supplier,fournisseur,cd}/mcp`: each fake as an MCP server
  (`mcp_server`), which a `demo` connector that carries a `url` reaches, or an `mcp` connector —
  `cd` is the environment that the train of a demo project promotes (S21-24);
- `/graph/v1.0/…` and `/login/{tenant}/oauth2/v2.0/token`: the fake Microsoft Graph, which an
  `entra` connector whose `graph_url` and `login_url` point at this service reaches;
- `/healthz`.

A token (`CONNECTOR_DEMO_TOKEN`) protects the MCP servers and is the fake Graph's application
secret; when empty, they ask for none: the deployment's network policy is then the only door, which
is fine for a demonstration only. The former variable name `CONNECTOR_DEMO_JETON` is still read.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from choregos_core.compat import EXTERNAL_ALIAS_REMOVAL

from .demo_cd import FakeDemoCd
from .entra import FakeEntra
from .hr import FAKES_BY_FAMILY, BusinessFake, FakeSupplier, mcp_server
from .mcp import FakeMcpServer

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class DemoApplication:
    def __init__(
        self,
        token: str = "",
        *,
        jeton: str | None = None,  # lang: fr-ok alias:internal
    ) -> None:
        if jeton is not None:  # lang: fr-ok alias:internal
            token = jeton  # lang: fr-ok alias:internal
        self.directory = FakeEntra(expected_secret=token)
        self.fakes: dict[str, BusinessFake] = {family: cls() for family, cls in FAKES_BY_FAMILY.items()}
        # `fournisseur` is the path a tenant configured before S39-11: a tenant name, kept.
        self.fakes["fournisseur"] = FakeSupplier()  # lang: fr-ok
        # The environment that the train of a demo project promotes (S21-24).
        self.fakes["cd"] = FakeDemoCd()
        self.servers: dict[str, FakeMcpServer] = {
            name: mcp_server(fake, token=token) for name, fake in self.fakes.items()
        }
        # S39-11: the supplier is reached at `/supplier/mcp`; `/fournisseur/mcp` (what a tenant
        # configured before) answers the same server, so both see one state.
        self.servers["supplier"] = self.servers["fournisseur"]  # lang: fr-ok

    @property
    def annuaire(self) -> FakeEntra:  # lang: fr-ok alias:internal
        return self.directory

    @property
    def faux(self) -> dict[str, BusinessFake]:  # lang: fr-ok alias:internal
        return self.fakes

    @property
    def serveurs(self) -> dict[str, FakeMcpServer]:  # lang: fr-ok alias:internal
        return self.servers

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/healthz":
            return httpx.Response(200, json={"ok": True})
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[1] == "mcp" and parts[0] in self.servers:
            return self.servers[parts[0]].repondre(request)
        if path.startswith("/login/"):
            return self.directory.respond(request)
        if path.startswith("/graph/"):
            unprefixed = request.url.copy_with(path=path.removeprefix("/graph"))
            return self.directory.respond(
                httpx.Request(request.method, unprefixed, headers=request.headers, content=request.content)
            )
        return httpx.Response(404, json={"error": f"nothing here: {path}"})

    repondre = respond  # lang: fr-ok alias:internal

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        request = httpx.Request(
            scope["method"],
            httpx.URL(path=scope["path"], query=scope.get("query_string", b"")).copy_with(
                scheme="http", host="demo"
            ),
            headers=[(key.decode("latin-1"), value.decode("latin-1")) for key, value in scope["headers"]],
            content=body,
        )
        response = self.respond(request)
        content = response.content
        headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in response.headers.items()
                   if k.lower() not in {"content-length", "transfer-encoding"}]  # fmt: skip
        headers.append((b"content-length", str(len(content)).encode()))
        await send({"type": "http.response.start", "status": response.status_code, "headers": headers})
        await send({"type": "http.response.body", "body": content})


def demo_token() -> str:
    """The token of the deployment: `CONNECTOR_DEMO_TOKEN`, else the former variable name."""
    token = os.environ.get("CONNECTOR_DEMO_TOKEN")
    if token is not None:
        return token
    former = os.environ.get("CONNECTOR_DEMO_JETON")  # lang: fr-ok alias:external
    if former is None:
        return ""
    warnings.warn(
        "CONNECTOR_DEMO_JETON is deprecated: use CONNECTOR_DEMO_TOKEN "  # lang: fr-ok alias:external
        f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
        DeprecationWarning,
        stacklevel=2,
    )
    return former


def application() -> DemoApplication:
    """The factory that `uvicorn --factory` calls."""
    return DemoApplication(token=demo_token())


def main() -> None:  # pragma: no cover - the container's entry point
    import uvicorn

    uvicorn.run(application(), host="0.0.0.0", port=int(os.environ.get("CHOREGOS_DEMO_PORT", "8090")))


if __name__ == "__main__":  # pragma: no cover - the container's entry point
    main()


# Former names that other code imports (ADR 0098 §3 (b)); `fakes/serveur.py` keeps the former path.
ApplicationDeDemo = DemoApplication  # lang: fr-ok alias:internal
