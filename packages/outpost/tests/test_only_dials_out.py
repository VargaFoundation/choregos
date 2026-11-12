# SPDX-License-Identifier: Apache-2.0
"""What the daemon cannot do, by construction (ADR 0147 §1, §9).

It runs in someone else's network. It opens every connection itself and listens on nothing; it
calls the door and the endpoints its policy file names, and no third place; it sends no telemetry;
it never updates itself; and no message of the door makes it download or run anything. A property
of that kind is not tested by a call that does not happen: it is read from the source, and from
what a running daemon was seen to dial.
"""

from __future__ import annotations

import ast
import io
import tomllib
from pathlib import Path

import httpx

from ._bench import bench

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / "choregos_outpost"
#: The scriptable door stands in for the platform in tests: it is not the daemon.
DAEMON = sorted(path for path in SOURCE.rglob("*.py") if "fakes" not in path.parts)

#: A socket that accepts: the daemon has none.
LISTENERS = {
    ".start_server",
    ".start_unix_server",
    ".create_server",
    ".create_unix_server",
    ".listen",
    ".bind",
}
#: Running another program, or code that arrived as data: a builtin by its name, a function of a
#: module by its owner and its name (`platform.system()` is not `os.system()`).
RUNNERS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "import_module",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.spawnv",
    "os.fork",
    "importlib.import_module",
}
RUNNER_MODULES = {"subprocess", "runpy", "pickle", "marshal", "ctypes", "pip", "ensurepip"}
#: Telemetry, another way out, or the platform's own code.
FOREIGN_MODULES = {"opentelemetry", "sentry_sdk", "prometheus_client", "requests", "aiohttp", "choregos_api"}


def _calls(tree: ast.AST) -> set[str]:
    """The names called: `f` for `f()`, and for `owner.f()` both `f` and `owner.f`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(f".{target.attr}")
            if isinstance(target.value, ast.Name):
                names.add(f"{target.value.id}.{target.attr}")
    return names


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_there_is_a_daemon_to_read() -> None:
    assert {path.name for path in DAEMON} >= {"daemon.py", "uplink.py", "toolhost.py", "doctor.py", "cli.py"}


def test_the_daemon_listens_on_nothing_and_runs_nothing() -> None:
    for path in DAEMON:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        where = path.relative_to(PACKAGE)
        assert not _calls(tree) & LISTENERS, f"{where} opens a socket that accepts: an outpost only dials out"
        assert not _calls(tree) & RUNNERS, f"{where} runs code or a program: no message of the door may"
        assert not _imports(tree) & RUNNER_MODULES, f"{where} imports what runs or loads code"
        assert not _imports(tree) & FOREIGN_MODULES, f"{where} imports telemetry, another client or the API"


def test_the_detector_sees_what_it_guards() -> None:
    listening = ast.parse("import asyncio\nasync def f():\n    await asyncio.start_server(h, '0.0.0.0', 1)\n")
    assert _calls(listening) & LISTENERS
    running = ast.parse("import subprocess\nsubprocess.run(['sh', '-c', order])\neval(order)\n")
    assert _calls(running) & RUNNERS and _imports(running) & RUNNER_MODULES
    assert _imports(ast.parse("from choregos_api.services import broker\n")) & FOREIGN_MODULES
    assert _imports(ast.parse("from opentelemetry import trace\n")) & FOREIGN_MODULES


def test_the_package_depends_on_nothing_that_serves_or_reports() -> None:
    declared = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {
        dependency.split(">")[0].split("=")[0].split("[")[0].strip().lower()
        for dependency in declared["dependencies"]
    }
    assert names == {
        "choregos-contracts",
        "choregos-core",
        "choregos-adapters",
        "httpx",
        "pydantic",
        "structlog",
        "pyyaml",
        "cryptography",
        "pyjwt",
        "jsonschema",
        "typer",
    }, "a new dependency of the daemon is a decision: say why in the pull request"
    assert declared["scripts"] == {"choregos-outpost": "choregos_outpost.cli:main"}


async def test_a_running_daemon_dials_the_door_and_its_endpoints_and_nothing_else(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    dialled: list[str] = []

    async def door(request: httpx.Request) -> httpx.Response:
        dialled.append(
            f"{request.method} {request.url.scheme}://{request.url.netloc.decode()}{request.url.path}"
        )
        return await b.door.handle(request)

    made = b.door.submit("tracker", "create_issue", {"title": "x"})
    refused = b.door.submit("warehouse", "search_issues", {})
    await b.start(transport=httpx.MockTransport(door))
    await b.door.answered(made.id)
    await b.door.refused(refused.id)
    await b.stop()
    # Four verbs of one origin, all of them requests the outpost opened.
    assert {line.rsplit("/", 1)[1] if "/calls/" in line else line for line in dialled} <= {
        "POST https://outposts.example.com/outposts/v1/poll",
        "answer",
        "refuse",
    }
    assert all(line.startswith("POST https://outposts.example.com/outposts/v1/") for line in dialled)
    # And one endpoint, the alias's own.
    assert {headers["host"] for _, headers, _ in b.server.received} == {"tracker-mcp.tools.svc:8080"}
