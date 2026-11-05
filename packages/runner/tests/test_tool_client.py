"""The generated client `choregos_tools` (ADR 0122 §3): its modules, and what each answer of the tool
bridge gives a script.

The bridge here is scripted (`ScriptedBridge`, a real HTTP server on the loopback): each test says what
it answers, in the shapes the internal API serves (`callRunTool`'s `{status_code, result, remaining,
suspicions}`, its problems), and reads what the client sent. The run's own bridge is exercised end to
end in `test_tool_scripts_run.py`.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from choregos_runner import tool_client, tool_scripts
from choregos_tools_mcp import bridge as tools_bridge
from choregos_tools_mcp import client as tools_client

RUN = "run-test-1"
#: `getRunTools` as the API lists it (`_run_tool`, `BrokerTool.announcement`): awkward names included.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "pfsense__list_aliases",
        "description": "[pfsense] Lists the aliases. Each alias names hosts, networks or ports.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
        "outputSchema": {"type": "object", "required": ["aliases"]},
        "annotations": {"readOnlyHint": True},
        "x-choregos-access": "read",
    },
    {
        "name": "pfsense__delete_firewall_rule",
        "description": '[pfsense] Deletes a rule """ by its tracker \\ id — needs a human approval: calling '
        "it proposes the action, it does not run it",
        "inputSchema": {"type": "object", "required": ["tracker"]},
        "annotations": {"readOnlyHint": False},
        "x-choregos-access": "approval",
    },
    {
        "name": "entra__disable_user",
        "description": "[entra] Disables an account — runs as a governed action",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": False},
        "x-choregos-access": "write",
    },
    {"name": "my-crm__class", "description": "Keyword", "inputSchema": {}, "x-choregos-access": "read"},
    {"name": "my-crm__2fa.reset", "description": "Digit first", "x-choregos-access": "write"},
    {"name": "my-crm__call", "description": "Takes the module's own name", "x-choregos-access": "read"},
    {
        "name": "my_crm__class",
        "description": "Another server, the same module name",
        "x-choregos-access": "read",
    },
    {"name": "search__find", "description": "A server named as the package's own `search`"},
    {"name": "web_fetch", "description": "Fetches a page.", "annotations": {"readOnlyHint": True}},
]


@pytest.fixture
def written(tmp_path: Path) -> tool_scripts.ToolClient:
    return tool_scripts.write_client(
        tmp_path / "tools", TOOLS, bridge="http://127.0.0.1:1/bridge/v1", run_id=RUN
    )


# ───────────────────────── the generated modules ─────────────────────────


def test_one_module_per_server_one_function_per_tool(written: tool_scripts.ToolClient) -> None:
    package = written.directory / tool_scripts.PACKAGE
    assert sorted(path.name for path in package.iterdir()) == [
        "__init__.py",
        "_index.json",
        "entra.py",
        "my_crm.py",
        "my_crm_2.py",
        "pfsense.py",
        "platform.py",
        "search_.py",
    ]
    functions = {
        module.stem: [
            node.name for node in ast.parse(module.read_text()).body if isinstance(node, ast.FunctionDef)
        ]
        for module in package.glob("*.py")
        if module.name != "__init__.py"
    }
    assert functions == {
        "pfsense": ["list_aliases", "delete_firewall_rule"],
        "entra": ["disable_user"],
        "my_crm": ["class_", "t_2fa_reset", "call_"],
        "my_crm_2": ["class_"],
        "search_": ["find"],
        "platform": ["web_fetch"],
    }
    # The package itself is the runner's source, copied: reviewed and tested once, not generated.
    assert (package / "__init__.py").read_bytes() == Path(tool_client.__file__).read_bytes()


def test_each_function_says_its_access_and_its_schema(written: tool_scripts.ToolClient) -> None:
    package = written.directory / tool_scripts.PACKAGE
    docs = {}
    for module in package.glob("*.py"):
        if module.name == "__init__.py":
            continue
        for node in ast.parse(module.read_text()).body:
            if isinstance(node, ast.FunctionDef):
                docs[f"{module.stem}.{node.name}"] = ast.get_docstring(node) or ""
                # Every function calls the tool by the name the platform lists it under.
                [call] = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
                assert isinstance(call.func, ast.Name) and call.func.id == "call"
    assert "Access: read." in docs["pfsense.list_aliases"]
    assert (
        'Input schema: {"type": "object", "properties": {"name": {"type": "string"}}}'
        in docs["pfsense.list_aliases"]
    )
    assert (
        'Returns (its output schema): {"type": "object", "required": ["aliases"]}'
        in docs["pfsense.list_aliases"]
    )
    assert "Access: approval — returns a Proposal; a person decides." in docs["pfsense.delete_firewall_rule"]
    # The description's own quotes and backslashes are text, not the end of the docstring.
    assert '"""' in docs["pfsense.delete_firewall_rule"] and "\\ id" in docs["pfsense.delete_firewall_rule"]
    assert docs["entra.disable_user"].startswith("[entra] Disables an account")
    assert "Access: write — a governed action" in docs["entra.disable_user"]
    assert "Access: read." in docs["platform.web_fetch"], "from readOnlyHint when the API says no access"
    assert "Access: unknown." in docs["search_.find"]


def test_the_modules_are_python_3_9(written: tool_scripts.ToolClient) -> None:
    """Every runner image and shell backend runs a Python of its own: the client asks for 3.9."""
    for module in (written.directory / tool_scripts.PACKAGE).glob("*.py"):
        source = module.read_text(encoding="utf-8")
        ast.parse(source, filename=str(module), feature_version=(3, 9))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or ".")])
        }
        assert imported <= set(sys.stdlib_module_names) | {""}, f"{module.name}: {imported}"


def test_the_index_has_a_line_per_tool(written: tool_scripts.ToolClient) -> None:
    lines = [
        line for line in (written.directory / "INDEX.md").read_text().splitlines() if line.startswith("- ")
    ]
    assert lines[0] == "- pfsense.list_aliases — read — Lists the aliases."
    assert lines[1].startswith("- pfsense.delete_firewall_rule — approval — Deletes a rule")
    assert lines[-1] == "- platform.web_fetch — read — Fetches a page."
    assert len(lines) == len(TOOLS)


def test_an_unreadable_tool_list_still_gives_a_client(tmp_path: Path) -> None:
    assert tool_scripts.tool_list(403, b'{"title": "Forbidden"}') == ([], "the run's tool list answered 403")
    assert tool_scripts.tool_list(200, b"[]")[1] == "the run's tool list is not a JSON object"
    client = tool_scripts.write_client(
        tmp_path / "tools", [], bridge="http://127.0.0.1:1/bridge/v1", run_id=RUN
    )
    assert client.tools == [] and (client.directory / "choregos_tools" / "__init__.py").is_file()
    assert "`call` still reaches a tool by its name" in (client.directory / "INDEX.md").read_text()


def test_the_client_speaks_the_bridge_s_language() -> None:
    """The client is standalone: what it shares with the bridge is checked here, not imported."""
    assert tool_client.SCRIPT_HEADER == tools_client.SCRIPT_HEADER
    assert tool_client.MAX_SCRIPT_BYTES == tools_bridge.MAX_SCRIPT_BYTES
    assert tool_scripts.BRIDGE_PATH == tools_bridge.PREFIX
    assert tool_scripts.bridge_url("http://localhost:7777/mcp") == "http://localhost:7777/bridge/v1"


# ───────────────────────── a scripted bridge ─────────────────────────

Answer = tuple[int, Any]


class ScriptedBridge:
    """The tool bridge, scripted: `answers[path]` is played in order, its last answer kept."""

    def __init__(self) -> None:
        self.answers: dict[str, list[Answer]] = {}
        self.requests: list[dict[str, Any]] = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                bridge.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": json.loads(body or b"null")}
                )
                queue = bridge.answers.get(
                    self.path.removeprefix("/bridge/v1"), [(404, {"detail": "Not Found"})]
                )
                status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
                content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                problem = isinstance(payload, dict) and "title" in payload
                self.send_header(
                    "content-type", "application/problem+json" if problem else "application/json"
                )
                self.send_header("content-length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/bridge/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def answer(self, tool: str, *answers: Answer) -> None:
        self.answers[f"/tools/{tool}"] = list(answers)

    def calls(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"].startswith("/bridge/v1/tools/")]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def bridge() -> Iterator[ScriptedBridge]:
    scripted = ScriptedBridge()
    yield scripted
    scripted.close()


@pytest.fixture
def tools(
    written: tool_scripts.ToolClient, bridge: ScriptedBridge, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ModuleType]:
    """`choregos_tools` as a script imports it, in this process: the runner's source as the package
    (so that coverage counts it; the copy is the same bytes), its generated modules beside it."""
    monkeypatch.setenv(tool_client.BRIDGE_VARIABLE, bridge.url)
    spec = importlib.util.spec_from_file_location(
        "choregos_tools",
        tool_client.__file__,
        submodule_search_locations=[str(written.directory / tool_scripts.PACKAGE)],
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "choregos_tools", package)
    spec.loader.exec_module(package)
    # The package's own functions read their module's globals: the delays and the script finder too.
    package.RETRY_DELAYS = (0.0, 0.0, 0.0)  # type: ignore[attr-defined]
    package.running_script = lambda: None  # type: ignore[attr-defined]
    yield package
    for name in [name for name in sys.modules if name.startswith("choregos_tools.")]:
        del sys.modules[name]


def _module(name: str) -> Any:
    return importlib.import_module(f"choregos_tools.{name}")


def _answer(result: Any, status_code: int = 200, **extra: Any) -> Answer:
    return 200, {"status_code": status_code, "result": result, "remaining": None, **extra}


# The results the internal API serves, as its code builds them.
#: A connector that is not MCP (a directory, pfSense): `broker._call` wraps what it returned.
DIRECTORY = {"aliases": [{"name": "PARTNERS", "content": ["0.0.0.0/0"]}], "count": 1}
DIRECTORY_RESULT = {
    "content": [{"type": "text", "text": json.dumps(DIRECTORY)}],
    "structuredContent": DIRECTORY,
}
#: An MCP server's result whose text is a summary and whose `structuredContent` is the data.
MCP_RESULT = {"content": [{"type": "text", "text": "1 alias: PARTNERS"}], "structuredContent": DIRECTORY}
#: An MCP server's result without `structuredContent` (a plugin that writes JSON in its text).
TEXT_JSON_RESULT = {"content": [{"type": "text", "text": '{"rules": [{"id": 17}]}'}], "isError": False}
#: Text that is not JSON.
TEXT_RESULT = {"content": [{"type": "text", "text": "3 rules — wan"}]}
#: Over the bound (`broker.bounded_result`): the text cut, `structuredContent` dropped.
CUT_RESULT = {
    "content": [
        {
            "type": "text",
            "text": '{"flows": [1, 2\n[truncated by the platform: 65000 of 99000 bytes; ask for less]',
        }
    ],
    "truncated": {"returned_bytes": 65000, "total_bytes": 99000},
}
#: `web_fetch` (`services/web_fetch.py`): the page's text in the text block, what was fetched in
#: `structuredContent`; the tool declares no output schema.
PAGE = "Flagpole licence\nFlagpole is released under the Apache-2.0 licence."
FETCHED = {
    "url": "https://flagpole.example.org/docs/licence",
    "status": 200,
    "content_type": "text/html",
    "bytes": 812,
    "truncated": False,
    "redirects": 0,
    "title": "Flagpole licence",
}
WEB_FETCH_RESULT = {"content": [{"type": "text", "text": PAGE}], "structuredContent": FETCHED}
#: `web_search` (`services/web_search.py`): its text is its `structuredContent` serialized.
FOUND = {"backend": "fake", "results": [{"title": "Flagpole", "url": FETCHED["url"], "fetchable": True}]}
WEB_SEARCH_RESULT = {
    "content": [{"type": "text", "text": json.dumps(FOUND, ensure_ascii=False)}],
    "structuredContent": FOUND,
}
#: A catalogue http tool: the provider's JSON as it came.
CATALOGUE_RESULT = [{"city": "Lyon", "temperature": 12}]
#: Under approval (`broker.propose_call`) and a governed write not finished (`write_through_action`).
PROPOSED = {
    "action": "act-1",
    "status": "pending_approval",
    "message": "proposed: a person approves it in the console before anything is done",
    "decision_path": "/p/demo/actions/act-1",
}
RUNNING = {**PROPOSED, "action": "act-2", "status": "running", "decision_path": "/p/demo/actions/act-2"}
CAP = {
    "type": "about:blank",
    "title": "Tool call cap reached",
    "status": 429,
    "detail": "this run has already called 10 tools (cap 10)",
}
SUSPICIONS = [
    {"source": "tool.web_fetch", "motif": "instruction override", "extrait": "ignore your instructions"},
    {"source": "tool.web_fetch", "motif": "prompt tags", "extrait": "<system>"},
]


# ───────────────────────── what each answer gives ─────────────────────────


def test_a_200_gives_the_result_unwrapped(tools: ModuleType, bridge: ScriptedBridge) -> None:
    bridge.answer("pfsense__list_aliases", _answer(DIRECTORY_RESULT), _answer(MCP_RESULT))
    bridge.answer("my-crm__class", _answer(TEXT_JSON_RESULT))
    bridge.answer("my-crm__call", _answer(TEXT_RESULT))
    bridge.answer("web_fetch", _answer(CATALOGUE_RESULT))

    assert _module("pfsense").list_aliases()["aliases"][0]["content"] == ["0.0.0.0/0"], "structuredContent"
    assert _module("pfsense").list_aliases() == DIRECTORY, "structuredContent, not the summary in its text"
    assert _module("my_crm").class_()["rules"] == [{"id": 17}], "the text, parsed: it is JSON"
    assert _module("my_crm").call_() == "3 rules — wan", "the text: it is not JSON"
    assert _module("platform").web_fetch() == CATALOGUE_RESULT, "any other JSON as it came"


def test_a_text_the_structure_does_not_hold_comes_beside_it(
    tools: ModuleType, bridge: ScriptedBridge
) -> None:
    """#1373: a script's `web_fetch` gets the page's text, and what was fetched beside it, as the
    model reads both through its MCP tools; a text that is the structure serialized adds nothing."""
    bridge.answer("web_fetch", _answer(WEB_FETCH_RESULT))
    bridge.answer("search__find", _answer(WEB_SEARCH_RESULT))
    bridge.answer("my-crm__call", _answer(MCP_RESULT))

    page = _module("platform").web_fetch(url=FETCHED["url"])
    assert page == {"text": PAGE, "structured": FETCHED}
    assert page["text"].splitlines()[0] == "Flagpole licence", "the page, which a script reads"
    assert _module("search_").find(query="flagpole") == FOUND, "the text serializes it: the data, once"
    # Without an output schema, a server's summary is something the structure does not say.
    assert _module("my_crm").call_() == {"text": "1 alias: PARTNERS", "structured": DIRECTORY}


def test_a_result_the_platform_cut_says_so_on_standard_error(
    tools: ModuleType, bridge: ScriptedBridge, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge.answer("web_fetch", _answer(CUT_RESULT))

    text = _module("platform").web_fetch(url="https://example.org")

    assert text.startswith('{"flows": [1, 2') and "[truncated by the platform" in text
    captured = capsys.readouterr()
    assert captured.err == (
        "[choregos] result of web_fetch was cut by the platform (65000 of 99000 bytes): "
        "ask for less (a filter, a page)\n"
    )
    assert captured.out == ""


def test_a_page_the_platform_cut_keeps_what_was_fetched_and_says_so(
    tools: ModuleType, bridge: ScriptedBridge, capsys: pytest.CaptureFixture[str]
) -> None:
    """#1383: the API bounds `web_fetch`'s answer; a page cut keeps what was fetched beside it, so a
    script's `["text"]` is still the page, cut, and standard error says so."""
    cut = PAGE[:20] + "\n[truncated by the platform: 20 of 70000 bytes; ask for less]"
    bridge.answer(
        "web_fetch",
        _answer(
            {
                "content": [{"type": "text", "text": cut}],
                "structuredContent": FETCHED,
                "truncated": {"returned_bytes": 20, "total_bytes": 70000},
            }
        ),
    )

    page = _module("platform").web_fetch(url=FETCHED["url"])

    assert page == {"text": cut, "structured": FETCHED}
    assert capsys.readouterr().err == (
        "[choregos] result of web_fetch was cut by the platform (20 of 70000 bytes): "
        "ask for less (a filter, a page)\n"
    )


def test_a_proposal_is_never_a_result(tools: ModuleType, bridge: ScriptedBridge) -> None:
    bridge.answer("pfsense__delete_firewall_rule", _answer(PROPOSED, 202, remaining=4))
    bridge.answer("entra__disable_user", _answer(RUNNING, 202))

    proposed = _module("pfsense").delete_firewall_rule(tracker="17")
    running = _module("entra").disable_user(upn="j.doe@example.org")

    assert isinstance(proposed, tools.Proposal) and isinstance(running, tools.Proposal)
    assert (proposed.action, proposed.status, proposed.decision_path) == (
        "act-1",
        "pending_approval",
        "/p/demo/actions/act-1",
    )
    assert proposed.proposed and not proposed.executed and proposed.message.startswith("proposed:")
    assert str(proposed) == (
        "PROPOSED, NOT DONE: pfsense__delete_firewall_rule (action act-1) — nothing was done; "
        "a person decides at /p/demo/actions/act-1"
    )
    assert repr(proposed).startswith("<Proposal PROPOSED, NOT DONE:"), "a printed list says it too"
    assert str(running).startswith("NOT FINISHED: entra__disable_user runs as governed action act-2")
    assert not running.proposed and not running.executed
    assert tools.remaining() is None, "the last answer's"


def test_a_withheld_result_raises(
    tools: ModuleType, bridge: ScriptedBridge, capsys: pytest.CaptureFixture[str]
) -> None:
    withheld = {"error": "result of web_fetch withheld: suspected injection — a human reviews it"}
    bridge.answer("web_fetch", _answer(withheld, 451, suspicions=SUSPICIONS))

    with pytest.raises(tools.Withheld) as raised:
        _module("platform").web_fetch(url="https://example.org")

    assert isinstance(raised.value, tools.ToolError)
    assert raised.value.status == 451 and raised.value.body == withheld
    assert [s["motif"] for s in raised.value.suspicions] == ["instruction override", "prompt tags"]
    assert "withheld as a suspected injection (instruction override, prompt tags)" in str(raised.value)
    assert "ignore your instructions" not in str(raised.value), "never a third party's words"
    assert capsys.readouterr().err.count("flagged as a suspected injection") == 2


def test_a_provider_s_error_raises_a_tool_error(tools: ModuleType, bridge: ScriptedBridge) -> None:
    refused = {"error": "pfsense unreachable: connection reset"}
    bridge.answer("pfsense__list_aliases", _answer(refused, 502))
    bridge.answer(
        "my-crm__class", _answer({"content": [{"type": "text", "text": "no such id"}], "isError": True})
    )

    with pytest.raises(tools.ToolError) as raised:
        _module("pfsense").list_aliases()
    with pytest.raises(tools.ToolError) as mcp_error:
        _module("my_crm").class_(id="x")

    assert not isinstance(raised.value, tools.CapReached | tools.Withheld)
    assert (raised.value.status, raised.value.body, raised.value.tool) == (
        502,
        refused,
        "pfsense__list_aliases",
    )
    assert str(raised.value) == "pfsense__list_aliases answered 502: pfsense unreachable: connection reset"
    assert "no such id" in str(mcp_error.value), "an MCP error result is an error, whatever its status"


def test_the_cap_raises_cap_reached(tools: ModuleType, bridge: ScriptedBridge) -> None:
    bridge.answer("pfsense__list_aliases", _answer(DIRECTORY_RESULT, remaining=1), (429, CAP))

    _module("pfsense").list_aliases()
    assert tools.remaining() == 1
    with pytest.raises(tools.CapReached) as raised:
        _module("pfsense").list_aliases()

    assert isinstance(raised.value, tools.ToolError) and raised.value.status == 429
    assert raised.value.remaining == 0 and tools.remaining() == 0
    assert "cap is reached" in str(raised.value) and "cap 10" in str(raised.value)


def test_a_problem_of_the_api_raises_a_tool_error(tools: ModuleType, bridge: ScriptedBridge) -> None:
    """A tool the run is not offered: the API's 404, relayed by the bridge as it came (G4)."""
    with pytest.raises(tools.ToolError) as raised:
        tools.call("pfsense__restore_everything")

    assert raised.value.status == 404 and not isinstance(raised.value, tools.CapReached)


def test_a_tool_server_not_ready_is_tried_again(tools: ModuleType, bridge: ScriptedBridge) -> None:
    unavailable = (503, {"title": "Tool bridge unavailable", "status": 503, "detail": "try again"})
    bridge.answer("pfsense__list_aliases", unavailable, unavailable, _answer(DIRECTORY_RESULT))
    bridge.answer("web_fetch", unavailable)

    assert _module("pfsense").list_aliases() == DIRECTORY
    assert len(bridge.calls()) == 3
    with pytest.raises(tools.ToolError) as raised:
        _module("platform").web_fetch()
    assert raised.value.status == 503 and len(bridge.calls()) == 3 + 1 + len(tool_client.RETRY_DELAYS)


def test_suspicions_go_to_standard_error(
    tools: ModuleType, bridge: ScriptedBridge, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge.answer("web_fetch", _answer(TEXT_RESULT, suspicions=[*SUSPICIONS, SUSPICIONS[0]]))

    assert _module("platform").web_fetch(url="https://example.org") == "3 rules — wan", "the result still"

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "[choregos] result of web_fetch flagged as a suspected injection: instruction override",
        "[choregos] result of web_fetch flagged as a suspected injection: prompt tags",
    ]
    assert captured.out == ""


def test_arguments_go_as_the_body_whatever_their_names(tools: ModuleType, bridge: ScriptedBridge) -> None:
    bridge.answer("my-crm__2fa.reset", _answer({"ok": True}))

    tools.call("my-crm__2fa.reset", tool="t", name="n", **{"e-mail": "a@b.c"})
    _module("my_crm").t_2fa_reset()

    first, second = bridge.calls()
    assert first["path"] == "/bridge/v1/tools/my-crm__2fa.reset"
    assert first["body"] == {"tool": "t", "name": "n", "e-mail": "a@b.c"}
    assert second["body"] == {}
    assert "X-Choregos-Script" not in first["headers"], "no script file, no digest"


def test_search_reads_the_index(tools: ModuleType) -> None:
    assert tools.search("alias", detail="name") == ["pfsense.list_aliases"]
    assert tools.search("firewall rule delete")[0] == (
        "pfsense.delete_firewall_rule — approval — " + tool_scripts.first_sentence(TOOLS[1]["description"])
    )
    [schema] = tools.search("disables", detail="schema")
    assert schema["tool"] == "entra__disable_user" and schema["access"] == "write"
    assert schema["inputSchema"] == {"type": "object"}
    assert len(tools.search("")) == len(TOOLS)
    with pytest.raises(ValueError):
        tools.search("alias", detail="everything")


def test_without_a_bridge_a_call_says_why(tools: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tool_client.BRIDGE_VARIABLE)
    tools._STATE.clear()
    with pytest.raises(RuntimeError, match="CHOREGOS_TOOLS_BRIDGE is not set"):
        tools.call("web_fetch")


# ───────────────────────── a script, as the agent runs it ─────────────────────────

SCRIPT = """\
from choregos_tools import pfsense, platform

aliases = pfsense.list_aliases()["aliases"]
page = platform.web_fetch(url="https://example.org/" + aliases[0]["name"])
print(len(aliases), page)
"""


def _run(command: list[str], client: tool_scripts.ToolClient, bridge: ScriptedBridge, **kwargs: Any) -> Any:
    """A script run as the agent's shell runs it: `-S`, so that only the standard library and
    `PYTHONPATH` are importable — the client needs nothing else."""
    env = tool_scripts.agent_environment(
        tool_scripts.ToolClient(client.directory, bridge.url, client.tools), {"PYTHONPATH": "/opt/kept"}
    )
    return subprocess.run(
        [sys.executable, "-S", *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        **kwargs,
    )


def test_a_script_sends_its_text_once_and_its_digest_with_every_call(
    written: tool_scripts.ToolClient, bridge: ScriptedBridge, tmp_path: Path
) -> None:
    bridge.answer("pfsense__list_aliases", _answer(DIRECTORY_RESULT))
    bridge.answer("web_fetch", _answer(TEXT_RESULT))
    bridge.answers["/scripts"] = [(201, {"journaled": True})]
    script = tmp_path / "audit.py"
    script.write_text(SCRIPT, encoding="utf-8")

    ran = _run([str(script)], written, bridge)

    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "1 3 rules — wan\n" and ran.stderr == "", "what the script prints, nothing else"
    digest = hashlib.sha256(SCRIPT.encode()).hexdigest()
    posted, *calls = bridge.requests
    assert posted["path"] == "/bridge/v1/scripts" and posted["body"] == {"sha256": digest, "text": SCRIPT}
    assert [c["path"] for c in calls] == [
        "/bridge/v1/tools/pfsense__list_aliases",
        "/bridge/v1/tools/web_fetch",
    ]
    assert all(c["headers"]["X-Choregos-Script"] == digest for c in calls)
    assert calls[1]["body"] == {"url": "https://example.org/PARTNERS"}, "the second call used the first"


def test_inline_code_is_a_script_too_and_standard_input_is_not(
    written: tool_scripts.ToolClient, bridge: ScriptedBridge
) -> None:
    bridge.answer("web_fetch", _answer(TEXT_RESULT))
    code = "from choregos_tools import platform; print(platform.web_fetch())"

    inline = _run(["-c", code], written, bridge)
    piped = _run(["-"], written, bridge, input=code)

    assert inline.returncode == 0 and piped.returncode == 0, (inline.stderr, piped.stderr)
    digest = hashlib.sha256(code.encode()).hexdigest()
    first, second, third = bridge.requests
    assert first["path"] == "/bridge/v1/scripts" and first["body"]["sha256"] == digest
    assert second["headers"]["X-Choregos-Script"] == digest
    assert third["path"] == "/bridge/v1/tools/web_fetch" and "X-Choregos-Script" not in third["headers"]


def test_a_script_too_long_to_keep_still_sends_its_digest(
    written: tool_scripts.ToolClient, bridge: ScriptedBridge, tmp_path: Path
) -> None:
    bridge.answer("web_fetch", _answer(TEXT_RESULT))
    text = (
        "# " + "x" * tool_client.MAX_SCRIPT_BYTES + "\nfrom choregos_tools import call\ncall('web_fetch')\n"
    )
    script = tmp_path / "long.py"
    script.write_text(text, encoding="utf-8")

    ran = _run([str(script)], written, bridge)

    assert ran.returncode == 0, ran.stderr
    [only] = bridge.requests
    assert only["headers"]["X-Choregos-Script"] == hashlib.sha256(text.encode()).hexdigest()


def test_the_environment_keeps_an_existing_python_path(written: tool_scripts.ToolClient) -> None:
    env = tool_scripts.agent_environment(written, {"PYTHONPATH": "/opt/kept"})
    assert env["PYTHONPATH"].split(":") == [str(written.directory), "/opt/kept"]
    assert env[tool_scripts.DIRECTORY_VARIABLE] == str(written.directory)
    assert env[tool_scripts.BRIDGE_VARIABLE] == written.bridge
    assert tool_scripts.agent_environment(written, {})["PYTHONPATH"] == str(written.directory)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("list-firewall.rules", "list_firewall_rules"),
        ("__init__", "init__"),
        ("", "tool"),
        ("lambda", "lambda_"),
    ],
)
def test_identifiers(name: str, expected: str) -> None:
    assert tool_scripts.identifier(name) == expected
    assert expected.isidentifier()
