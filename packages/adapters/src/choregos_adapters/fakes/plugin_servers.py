# SPDX-License-Identifier: Apache-2.0
"""A plugin's server on its fake, as an eval matrix connector (#1372, ADR 0096, ADR 0122 §9).

A matrix case may name a plugin's server as a connector: `{kind: "plugin:<plugin>/<server>", scene:
<name>}` (`choregos_core.plugins`, `contents.matrix_cases`). A plugin's code runs only as its server's
image (ADR 0093 §2), so the core never imports it: the server is reached the way the broker reaches it,
over MCP with its bearer token (`PluginServerClient`), and what answers is the server's FAKE.

- **Scripted** (`plugin_scene`, the eval matrix's `EvalApi`): the fake is a transport registered for the
  server (`use_fake_plugin_server`): a test's, or a plugin's own CI's, which serves the server's code
  in-process on its fake instance (an `httpx.ASGITransport` over its app). Core's CI registers
  `FakePluginServer`, which answers from a recording of the server's fake (a fixture). Without one, the
  case cannot be played and says so. `plugin_scene` lists the server's tools and keeps those its
  declaration declares (a tool it does not declare is refused, ADR 0096), each with the declared access,
  `human_approval` and platform arguments, and the schema the server lists.
- **Live** (`bound_plugin_fake`): the API runs, beside a server that declares its `fake` (the variables
  that switch it to its fake, the scenes it serves), an EVAL INSTANCE of it: the same image with those
  variables, no configuration and no egress, and only where the deployment serves eval fakes
  (`CHOREGOS_EVAL_FAKE_SCENES`). A connector reaches it only when the matrix's seeding bound it
  (`fake_scene: <scene>` in its configuration, refused on every declaration and edit, as for a scene,
  S57-11a); without the switch the field is ignored and the connector is the server's own instance.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..errors import AdapterError
from ..registry import FAKE_SCENE_FIELD, OperationSpec

#: How a case names a plugin's server (`plugin:<plugin>/<server>`).
PLUGIN_KIND = "plugin:"
#: The eval matrix's token for a server on its fake: the fake checks it as a server checks the platform's.
EVAL_TOKEN = "eval-plugin-server-token"
#: The address a scripted fake answers on: the fake's transport, never the network.
EVAL_URL = "http://{server}.plugin-fake.invalid/mcp"

#: (plugin, server) -> a transport serving the server's fake for a scene.
FakeServerFactory = Callable[[str], httpx.AsyncBaseTransport]
_FAKES: dict[tuple[str, str], FakeServerFactory] = {}


def plugin_kind(kind: str) -> tuple[str, str] | None:
    """`plugin:<plugin>/<server>` as `(plugin, server)`; `None` for any other kind."""
    if not kind.startswith(PLUGIN_KIND):
        return None
    plugin, _, server = kind.removeprefix(PLUGIN_KIND).partition("/")
    return (plugin, server) if plugin and server and "/" not in server else None


def use_fake_plugin_server(plugin: str, server: str, factory: FakeServerFactory | None) -> None:
    """Registers what serves `<plugin>/<server>` on its fake in the scripted matrix (`None`: removed).
    `factory(scene)` is a transport the server's MCP endpoint answers on, a fresh fake per call."""
    _FAKES.pop((plugin, server), None)
    if factory is not None:
        _FAKES[(plugin, server)] = factory


def fake_plugin_server(plugin: str, server: str, scene: str) -> httpx.AsyncBaseTransport:
    """A fresh transport to `<plugin>/<server>` on its fake for `scene`; `AdapterError` when none is
    registered."""
    factory = _FAKES.get((plugin, server))
    if factory is None:
        raise AdapterError(
            f"no fake of the plugin server {plugin}/{server} in this process: the scripted matrix plays a "
            "plugin's server only on a fake registered for it (`use_fake_plugin_server`: a test, or the "
            "plugin's own CI with its server in-process); a live cell plays it on its eval instance"
        )
    return factory(scene)


def declared_scenes(declaration: Mapping[str, Any]) -> tuple[str, ...]:
    """The scenes a server's fake serves (its `fake.scenes`, `default` when absent); none without one."""
    from choregos_core.plugins import fake_scenes

    return fake_scenes(dict(declaration))


@dataclass
class PluginScene:
    """A plugin's server on its fake: what the broker calls (`connector`, the server's MCP client), the
    fake a test reads (`fake`, what the factory's transport serves, when it says), and the operations
    its declaration governs."""

    kind: str
    name: str
    connector: Any
    fake: Any
    operations: tuple[OperationSpec, ...]
    declaration: dict[str, Any] = field(default_factory=dict)


async def plugin_scene(kind: str, name: str, declaration: Mapping[str, Any] | None) -> PluginScene:
    """`kind` (`plugin:<plugin>/<server>`) on its fake for the scene `name`, governed by `declaration`
    (the server's file): its tools as the server lists them, those its `operations` declare only."""
    from ..plugin_servers import PluginServerClient

    named = plugin_kind(kind)
    if named is None:
        raise AdapterError(f"`{kind}` is not a plugin's server (`plugin:<plugin>/<server>`)")
    if declaration is None:
        raise AdapterError(
            f"no plugin read by this process ships the server {named[0]}/{named[1]}: the matrix finds a "
            "plugin's server in the offline plugins (`CHOREGOS_PLUGINS_OFFLINE`)"
        )
    transport = fake_plugin_server(named[0], named[1], name)
    connector = PluginServerClient(
        EVAL_URL.format(server=named[1]), token=EVAL_TOKEN, transport=transport, timeout_s=30.0
    )
    listed = {tool.name: tool for tool in await connector.list_tools()}
    operations: list[OperationSpec] = []
    for op in declaration.get("operations") or []:
        tool = listed.get(str(op["name"]))
        schema = op.get("input_schema") or (tool.input_schema if tool is not None else None)
        operations.append(
            OperationSpec(
                str(op["name"]),
                str(op["access"]),
                str(op.get("description") or (tool.description if tool is not None else "")),
                dict(schema) if schema else None,
                human_approval=bool(op.get("human_approval")),
                compensation=op.get("compensation"),
                platform_arguments=tuple(str(a) for a in op.get("platform_arguments") or ()),
            )
        )
    fake = getattr(transport, "fake", None)
    return PluginScene(kind, name, connector, fake, tuple(operations), dict(declaration))


def bound_plugin_fake(declaration: Mapping[str, Any] | None, config: Mapping[str, Any] | None) -> str | None:
    """The scene of its fake a plugin server's connector is bound to (`fake_scene`, written by the eval
    matrix's seeding only), or `None`: when its configuration names none, when the server declares no
    fake of that scene, and when the deployment does not serve eval fakes — the field is then ignored and
    the connector is the server's own instance (#1372)."""
    from .scenes import eval_fake_scenes_allowed

    scene = (config or {}).get(FAKE_SCENE_FIELD)
    if not scene or declaration is None or not eval_fake_scenes_allowed():
        return None
    return str(scene) if str(scene) in declared_scenes(declaration) else None


def _canonical(arguments: Mapping[str, Any], ignored: frozenset[str]) -> str:
    kept = {k: v for k, v in arguments.items() if k not in ignored}
    return json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class FakePluginServer:
    """A plugin's server answering MCP over HTTP (`initialize`, `tools/list`, `tools/call`) from a
    RECORDING of its own fake: the tools it lists, and the answer to each call it was recorded making.

    A call it was not recorded making is an MCP error result (`isError`), never a guess. The platform's
    arguments (`action_key`, `work_item_key`) differ per action and are left out of the match. It
    records every call (`calls`), so a test reads which writes reached the server, and every request
    (`requests`), so a test sees the bearer token; with `token`, a call without it is a 401."""

    def __init__(
        self,
        tools: list[dict[str, Any]],
        answers: list[dict[str, Any]],
        *,
        name: str = "fake-plugin-server",
        token: str | None = EVAL_TOKEN,
        platform_arguments: frozenset[str] = frozenset({"action_key", "work_item_key"}),
    ) -> None:
        self.name = name
        self.token = token
        self.tools = [dict(tool) for tool in tools]
        self.ignored = platform_arguments
        self.answers: dict[tuple[str, str], dict[str, Any]] = {
            (str(a["tool"]), _canonical(dict(a.get("arguments") or {}), platform_arguments)): dict(
                a["result"]
            )
            for a in answers
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[httpx.Request] = []

    @classmethod
    def from_recording(cls, path: Path, **options: Any) -> FakePluginServer:
        """A recording file: `{server, tools: [...], answers: [{tool, arguments, result}]}`."""
        recorded = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            list(recorded["tools"]), list(recorded["answers"]), name=str(recorded["server"]), **options
        )

    def transport(self) -> FakeTransport:
        return FakeTransport(self)

    def called(self, tool: str) -> list[dict[str, Any]]:
        """The arguments of every call of `tool` that reached the server, in order."""
        return [arguments for name, arguments in self.calls if name == tool]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.token is not None and request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "the platform's bearer token is required"})
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name},
            }
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "tools/call":
            params = body.get("params") or {}
            result = self._call(str(params.get("name")), dict(params.get("arguments") or {}))
        else:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        answer = self.answers.get((tool, _canonical(arguments, self.ignored)))
        if answer is None:
            text = f"`{tool}` was not recorded with these arguments: the fake answers its recording only"
            return {"isError": True, "content": [{"type": "text", "text": text}]}
        copied: dict[str, Any] = json.loads(json.dumps(answer))
        return copied


class FakeTransport(httpx.MockTransport):
    """The transport of a `FakePluginServer`, which it hands a test (`plugin_scene` reads `fake`)."""

    def __init__(self, fake: FakePluginServer) -> None:
        super().__init__(fake._handle)
        self.fake = fake


__all__ = [
    "EVAL_TOKEN",
    "PLUGIN_KIND",
    "FakePluginServer",
    "FakeServerFactory",
    "FakeTransport",
    "PluginScene",
    "bound_plugin_fake",
    "declared_scenes",
    "fake_plugin_server",
    "plugin_kind",
    "plugin_scene",
    "use_fake_plugin_server",
]
