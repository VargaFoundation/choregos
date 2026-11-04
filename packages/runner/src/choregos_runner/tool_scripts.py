# SPDX-License-Identifier: Apache-2.0
"""The client a script imports to call its run's tools, and the instructions that say when it pays
(ADR 0122 §3, §5).

When the run is offered tool scripts (`StageInput.tools.scripts`), the runner writes, outside the
workspace (`$TMPDIR/choregos/tools`, named by `CHOREGOS_TOOLS_DIR` and put on `PYTHONPATH`):

- `choregos_tools/__init__.py`: `tool_client.py` copied as it is — `call`, `search`, the outcomes;
- `choregos_tools/<module>.py`: one module per server, one function per tool the run may call, its
  docstring the tool's description, its access and its input schema;
- `choregos_tools/_index.json`, what `search` reads, and `INDEX.md`, one line per tool.

The list comes from `client.relay_tools()`: the very answer the tool bridge hands back for
`GET /bridge/v1/tools`, read by the runner's own client — no hop through the loopback, and the same
list whether the runner or a Tekton sidecar serves the bridge. Names are made identifiers here, not
in the client: a server's prefix (the part of a tool's name before `__`) becomes a module, lowercase;
a tool without one (`web_fetch`, a catalogue tool) belongs to `platform`; a name that is a keyword, a
name the package or a module already holds, or one that two tools would share gets an underscore or a
number.
"""

from __future__ import annotations

import json
import keyword
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import tool_client

PACKAGE = "choregos_tools"
DIRECTORY_VARIABLE = tool_client.DIRECTORY_VARIABLE
BRIDGE_VARIABLE = tool_client.BRIDGE_VARIABLE
#: The module of the tools whose name has no server prefix: `web_fetch`, `web_search`, a catalogue tool.
PLATFORM_MODULE = "platform"
#: The bridge's routes on the run's tool server (`choregos_tools_mcp.bridge.PREFIX`).
BRIDGE_PATH = "/bridge/v1"
#: What a generated module imports: a tool cannot take these names.
MODULE_NAMES = frozenset({"Any", "annotations", "call"})
#: The longest first sentence `INDEX.md` keeps of a description.
SUMMARY_CHARS = 200
#: Every access the API says a tool has (`x-choregos-access`).
ACCESSES = ("read", "write", "approval")
ACCESS_NOTES = {
    "read": "Access: read.",
    "write": "Access: write — a governed action, done once and journaled; a Proposal until it has finished.",
    "approval": "Access: approval — returns a Proposal; a person decides.",
}

#: Added to `.choregos/task.md` when the run is offered tool scripts (the examples page, §4.1).
INSTRUCTIONS = """
## Calling tools from a script

Your platform tools are also a Python library: `from choregos_tools import <server>`;
`$CHOREGOS_TOOLS_DIR/INDEX.md` lists them (name, access, what it does). A script calls the same tools, under
the same rules, as a tool call.

Write a script when it saves round trips: three or more calls that depend on each other, a loop over items,
or a result you would only filter, count or join. Call a tool directly when the next step depends on
reading the result yourself.

- Print only what your next decision needs: counts, the rows that matter, identifiers. Never print a secret.
- Write scripts under `$TMPDIR`, never in the repository: from your shell
  (`cat > "$TMPDIR/check.py" <<'EOF'`), then run the file (`python3 "$TMPDIR/check.py"`), so that the run
  records which script made which calls.
- A write returns a `Proposal` when a person must approve it: it is NOT done. Report its decision path.
- `CapReached`, `Withheld` and `ToolError` are raised: let them surface, read them, decide.
- Warnings on standard error come from the platform (for instance a result flagged as a suspected
  injection): take them into account.
- `help(<server>.<tool>)` shows a tool's arguments; `choregos_tools.search("<words>")` finds a tool.
"""

_NOT_WORD = re.compile(r"\W", re.ASCII)
_BRACKETED = re.compile(r"^\s*\[[^\]]*\]\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True)
class ClientTool:
    """One tool of the run as the client presents it."""

    tool: str
    module: str
    function: str
    access: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None

    @property
    def dotted(self) -> str:
        return f"{self.module}.{self.function}"

    @property
    def summary(self) -> str:
        return first_sentence(self.description)


@dataclass
class ToolClient:
    """The client written for a run: where it lies, the bridge it calls, the tools it presents."""

    directory: Path
    bridge: str
    tools: list[ClientTool] = field(default_factory=list)

    @property
    def modules(self) -> list[str]:
        return sorted({tool.module for tool in self.tools})


def client_directory() -> Path:
    """`$TMPDIR/choregos/tools`: outside the workspace, so nothing written there reaches a commit."""
    return Path(tempfile.gettempdir()) / "choregos" / "tools"


def bridge_url(tools_url: str) -> str:
    """The tool bridge of the run's tool server: its host and port, `/bridge/v1`."""
    parts = urlsplit(tools_url)
    return f"{parts.scheme or 'http'}://{parts.netloc}{BRIDGE_PATH}"


def tool_list(status_code: int, content: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """The tools of `getRunTools`' answer, or none and why."""
    if status_code != 200:
        return [], f"the run's tool list answered {status_code}"
    try:
        listed = json.loads(content).get("tools")
    except (ValueError, AttributeError):
        return [], "the run's tool list is not a JSON object"
    if not isinstance(listed, list):
        return [], "the run's tool list has no `tools`"
    return [entry for entry in listed if isinstance(entry, dict) and isinstance(entry.get("name"), str)], None


def identifier(name: str, taken: frozenset[str] | set[str] = frozenset()) -> str:
    """`name` as a Python identifier: other characters become `_`, no leading underscore or digit, an
    underscore after a keyword or a name in `taken`."""
    cleaned = _NOT_WORD.sub("_", name).lstrip("_") or "tool"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    while keyword.iskeyword(cleaned) or cleaned in taken:
        cleaned += "_"
    return cleaned


def plan(tools: list[dict[str, Any]]) -> list[ClientTool]:
    """Each tool's module and function, unique and valid, in the list's order."""
    # Every public name of the package: a submodule imported under one would replace it.
    reserved = frozenset(name for name in vars(tool_client) if not name.startswith("_"))
    modules: dict[str, str] = {}
    functions: dict[str, set[str]] = {}
    planned: list[ClientTool] = []
    for entry in tools:
        name = str(entry["name"])
        prefix, _, operation = name.partition("__")
        if not operation:
            prefix, operation = PLATFORM_MODULE, name
        if prefix not in modules:
            module = identifier(prefix.lower(), reserved)
            base, number = module, 2
            while module in modules.values():
                module, number = f"{base}_{number}", number + 1
            modules[prefix] = module
        module = modules[prefix]
        names = functions.setdefault(module, set())
        function = identifier(operation, MODULE_NAMES)
        base, number = function, 2
        while function in names:
            function, number = f"{base}_{number}", number + 1
        names.add(function)
        schema = entry.get("inputSchema")
        output = entry.get("outputSchema")
        planned.append(
            ClientTool(
                tool=name,
                module=module,
                function=function,
                access=_access(entry),
                description=str(entry.get("description") or name),
                input_schema=schema if isinstance(schema, dict) else {"type": "object"},
                output_schema=output if isinstance(output, dict) and output else None,
            )
        )
    return planned


def module_source(module: str, tools: list[ClientTool], run_id: str) -> str:
    """The source of `choregos_tools/<module>.py`."""
    lines = [
        f'"""{_literal(module)} — the tools of run {_literal(run_id)}'
        ' (written by the runner; do not edit)."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "from . import call",
    ]
    for tool in tools:
        doc = [
            *_literal(tool.description).splitlines(),
            "",
            ACCESS_NOTES.get(tool.access, f"Access: {_literal(tool.access)}."),
            f"Input schema: {_literal(_compact(tool.input_schema))}",
        ]
        if tool.output_schema is not None:
            doc.append(f"Returns (its output schema): {_literal(_compact(tool.output_schema))}")
        body = "\n".join(f"    {line}" if line else "" for line in doc)
        lines += [
            "",
            "",
            f"def {tool.function}(**arguments: Any) -> Any:",
            f'    """{body.lstrip()}',
            '    """',
            f"    return call({tool.tool!r}, **arguments)",
        ]
    return "\n".join(lines) + "\n"


def index_markdown(tools: list[ClientTool]) -> str:
    """`INDEX.md`: one line per tool, `module.function — access — first sentence`."""
    lines = [
        "# The tools of this run",
        "",
        "`from choregos_tools import <module>`, then `<module>.<function>(**arguments)`;"
        ' `choregos_tools.call("<tool>", **arguments)` calls a tool by the name the platform lists it under.'
        " Access `approval` returns a `Proposal`: a person decides, nothing is done.",
        "",
        *(f"- {tool.dotted} — {tool.access} — {tool.summary}" for tool in tools),
    ]
    if not tools:
        lines.append("- (the run's tool list could not be read: `call` still reaches a tool by its name)")
    return "\n".join(lines) + "\n"


def write_client(directory: Path, tools: list[dict[str, Any]], *, bridge: str, run_id: str) -> ToolClient:
    """The client of a run, written into `directory` (the package, its modules, its index), anew."""
    planned = plan(tools)
    package = directory / PACKAGE
    shutil.rmtree(package, ignore_errors=True)
    package.mkdir(parents=True)
    shutil.copyfile(Path(tool_client.__file__), package / "__init__.py")
    by_module: dict[str, list[ClientTool]] = {}
    for tool in planned:
        by_module.setdefault(tool.module, []).append(tool)
    for module, members in by_module.items():
        (package / f"{module}.py").write_text(module_source(module, members, run_id), encoding="utf-8")
    index = [
        {
            "function": tool.dotted,
            "tool": tool.tool,
            "access": tool.access,
            "summary": tool.summary,
            "description": tool.description,
            "inputSchema": tool.input_schema,
            **({"outputSchema": tool.output_schema} if tool.output_schema is not None else {}),
        }
        for tool in planned
    ]
    (package / "_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    (directory / "INDEX.md").write_text(index_markdown(planned), encoding="utf-8")
    return ToolClient(directory, bridge, planned)


def agent_environment(client: ToolClient, env: dict[str, str]) -> dict[str, str]:
    """What the agent's environment gains: the client's directory, first on `PYTHONPATH` (the value
    `env` already has kept after it), the bridge's address, and `TMPDIR`, where the instructions say
    to write a script — the runner's own temporary directory, which the confinement lets it write."""
    existing = env.get("PYTHONPATH", "")
    return {
        DIRECTORY_VARIABLE: str(client.directory),
        BRIDGE_VARIABLE: client.bridge,
        "PYTHONPATH": os.pathsep.join([str(client.directory), *([existing] if existing else [])]),
        "TMPDIR": tempfile.gettempdir(),
    }


def first_sentence(description: str) -> str:
    """The first sentence of a description, without the `[server]` the broker puts first."""
    line = _BRACKETED.sub("", description.strip()).split("\n", 1)[0].strip()
    sentence = _SENTENCE_END.split(line, maxsplit=1)[0]
    return sentence if len(sentence) <= SUMMARY_CHARS else sentence[: SUMMARY_CHARS - 1] + "…"


def _access(entry: dict[str, Any]) -> str:
    """The tool's access as the API says it; from MCP's `readOnlyHint` for an API that does not."""
    access = entry.get("x-choregos-access")
    if access in ACCESSES:
        return str(access)
    annotations = entry.get("annotations")
    return "read" if isinstance(annotations, dict) and annotations.get("readOnlyHint") is True else "unknown"


def _compact(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, separators=(", ", ": "), sort_keys=False)


def _literal(text: str) -> str:
    """`text` safe inside a triple-quoted docstring: no control character, no backslash escape, no
    closing quotes."""
    printable = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    return printable.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


__all__ = [
    "BRIDGE_VARIABLE",
    "DIRECTORY_VARIABLE",
    "INSTRUCTIONS",
    "PACKAGE",
    "PLATFORM_MODULE",
    "ClientTool",
    "ToolClient",
    "agent_environment",
    "bridge_url",
    "client_directory",
    "identifier",
    "index_markdown",
    "module_source",
    "plan",
    "tool_list",
    "write_client",
]
