# SPDX-License-Identifier: Apache-2.0
"""The tools of this run as a Python library (ADR 0122 §3): `from choregos_tools import <server>`.

Copied by the runner, for each run offered tool scripts, to `$CHOREGOS_TOOLS_DIR/choregos_tools/`,
outside the repository, beside one module per server (`<server>.py`, one function per tool) and
`$CHOREGOS_TOOLS_DIR/INDEX.md`. Do not edit the copy: the next run writes it again. Standard library
only, Python 3.9 or later.

Every call goes through the runner's tool bridge (`$CHOREGOS_TOOLS_BRIDGE`), which holds the run's
credential; the script holds none. The platform governs a call from a script exactly as a tool call of
the model: the same allowlist, cap, approvals, bounds, masking, injection guard and ledger.

What a call gives the script:

    The platform answered              The script gets
    ---------------------------------  ------------------------------------------------------------
    200                                the tool's result, unwrapped (below)
    202 naming an action               a `Proposal`: proposed, or a governed write still running —
                                       NOT done
    429 "Tool call cap reached"        `CapReached` raised; `remaining()` is 0
    451                                `Withheld` raised: the result was withheld as a suspected
                                       injection
    any other status of 400 or more    `ToolError(status, body)` raised, the tool's own error
                                       answer included
    `suspicions` beside a result       the result, and one line per suspicion on standard error

A result is unwrapped by one rule:

- an MCP result (`{"content": [...], ...}`, what a connector, `web_fetch` and `web_search` return)
  gives its `structuredContent` when it has one — what the tool's output schema describes; otherwise
  its text, parsed as JSON when the text is JSON, else the text itself. An MCP result marked
  `isError` raises `ToolError`;
- any other JSON (a catalogue tool's answer) is returned as it is.

A result over the platform's size bound comes back cut: it has no `structuredContent`, its text ends
with the platform's marker, and a line on standard error says so — ask for less (a filter, a page).

The first call of a process sends the running script's text to the bridge once, then its SHA-256
with every call, so that the run's journal says which script made which calls (ADR 0122 §4); a
process that is not a script file (`python3 -` reading standard input, an interactive session) sends
neither. When the tool server is not ready yet (503), or nothing listens yet, a call is tried again a
few times, a little later each time.
"""

from __future__ import annotations

import contextlib as _contextlib
import hashlib as _hashlib
import json as _json
import os as _os
import sys as _sys
import threading as _threading
import time as _time
import urllib.error as _urlerror
import urllib.parse as _urlparse
import urllib.request as _urlrequest
from typing import Any as _Any

#: The tool bridge's address (`http://localhost:7777/bridge/v1`), set by the runner.
BRIDGE_VARIABLE = "CHOREGOS_TOOLS_BRIDGE"
#: The directory of this package and of `INDEX.md`, set by the runner.
DIRECTORY_VARIABLE = "CHOREGOS_TOOLS_DIR"
#: The header that carries the running script's digest.
SCRIPT_HEADER = "X-Choregos-Script"
#: The longest script text the bridge keeps (UTF-8 bytes); a longer one is not sent, its digest is.
MAX_SCRIPT_BYTES = 64 * 1024
#: How long one call may take, its wait for a place at the bridge included: the bridge answers 504
#: itself after 60 s of relaying, and runs a few calls of the run at a time.
TIMEOUT_S = 600.0
#: The pauses before each new try of a call that the tool server could not take yet.
RETRY_DELAYS = (0.5, 1.0, 2.0)
#: The status of an action a person decides before anything is done (ADR 0035).
PENDING_APPROVAL = "pending_approval"
#: The first words of every line this library writes on standard error.
WARNING_PREFIX = "[choregos]"

#: The bridge is on the loopback: a call never goes through the run's egress proxy.
_OPENER = _urlrequest.build_opener(_urlrequest.ProxyHandler({}))
_LOCK = _threading.Lock()
_STATE: dict[str, _Bridge] = {}


class ToolError(Exception):
    """A call the platform refused, or that the tool answered with an error.

    `status` is the status the platform answered, or the tool's own when the platform relayed its
    error answer; `body` is that answer, parsed when it is JSON; `tool` names the tool called."""

    def __init__(self, status: int, body: _Any, tool: str = "") -> None:
        self.status = status
        self.body = body
        self.tool = tool
        super().__init__(self._message())

    def _message(self) -> str:
        return f"{self.tool or 'the tool'} answered {self.status}: {_summary(self.body)}"


class CapReached(ToolError):  # noqa: N818 - the name ADR 0122 §3 gives the outcome
    """The run has made all the tool calls its policy allows (`budgets.tool_calls_per_run`): no other
    call of this run will be served, from a script or from the model."""

    remaining = 0

    def _message(self) -> str:
        called = self.tool or "the call"
        return f"the run's tool-call cap is reached, {called} was not made: {_summary(self.body)}"


class Withheld(ToolError):  # noqa: N818 - the name ADR 0122 §3 gives the outcome
    """The platform withheld the tool's result as a suspected injection (`sandbox.prompt_injection:
    block`): a person reviews it. `suspicions` says what was flagged."""

    def __init__(self, status: int, body: _Any, tool: str = "", suspicions: _Any = None) -> None:
        self.suspicions = list(suspicions) if isinstance(suspicions, list) else []
        super().__init__(status, body, tool)

    def _message(self) -> str:
        reasons = ", ".join(_reasons(self.suspicions)) or "unnamed"
        return f"the result of {self.tool or 'the tool'} was withheld as a suspected injection ({reasons})"


class Proposal:
    """A write that is NOT done.

    `status` `pending_approval`: the call proposed an action, and a person decides at `decision_path`
    (ADR 0035); nothing was done. Any other status: the write runs as a governed action that has not
    finished yet; its outcome will be at `decision_path`. Report the decision path, never the write as
    done."""

    #: Whatever its status, a proposal is not an executed write.
    executed = False

    def __init__(self, tool: str, action: str, status: str, decision_path: str, message: str = "") -> None:
        self.tool = tool
        self.action = action
        self.status = status
        self.decision_path = decision_path
        self.message = message

    @property
    def proposed(self) -> bool:
        """True when a person decides; False for a governed write that has not finished yet."""
        return self.status == PENDING_APPROVAL

    def __str__(self) -> str:
        if self.proposed:
            return (
                f"PROPOSED, NOT DONE: {self.tool} (action {self.action}) — nothing was done; "
                f"a person decides at {self.decision_path}"
            )
        return (
            f"NOT FINISHED: {self.tool} runs as governed action {self.action} (status {self.status}); "
            f"its outcome will be at {self.decision_path}"
        )

    def __repr__(self) -> str:
        return f"<Proposal {self}>"


def call(tool: str, /, **arguments: _Any) -> _Any:
    """Call the run's tool `tool`, by the name the platform lists it under (`<server>__<operation>`,
    `web_fetch`), with `arguments` as its input. An argument whose name is not a Python identifier
    goes through a dictionary: `call("crm__find", **{"e-mail": "a@b.c"})`."""
    return _bridge().call(tool, arguments)


def search(query: str, /, detail: str = "summary") -> list[_Any]:
    """The run's tools whose name or description holds the words of `query`, best first (all of them
    for an empty query): `detail="name"` gives `module.function` names, `"summary"` the lines of
    `INDEX.md`, `"schema"` a dictionary per tool with its description and its input schema."""
    if detail not in ("name", "summary", "schema"):
        raise ValueError(f'detail must be "name", "summary" or "schema", not {detail!r}')
    words = query.lower().split()
    scored: list[tuple[int, int, dict[str, _Any]]] = []
    for position, entry in enumerate(_index()):
        text = " ".join(str(entry.get(key, "")) for key in ("function", "tool", "description")).lower()
        score = sum(1 for word in words if word in text)
        if score or not words:
            scored.append((-score, position, entry))
    found = [entry for _, _, entry in sorted(scored, key=lambda item: (item[0], item[1]))]
    if detail == "name":
        return [entry["function"] for entry in found]
    if detail == "summary":
        return [f"{entry['function']} — {entry['access']} — {entry['summary']}" for entry in found]
    return [{key: value for key, value in entry.items() if key != "summary"} for entry in found]


def remaining() -> int | None:
    """The tool calls the run's cap still allows, as the last answer said; None without a cap, or
    before the first call."""
    return _bridge().remaining


class _Bridge:
    """The tool bridge of the run, as this process reaches it."""

    def __init__(self, url: str) -> None:
        if _urlparse.urlsplit(url).scheme not in ("http", "https"):
            raise RuntimeError(f"{BRIDGE_VARIABLE} must be an http address, not {url[:80]!r}")
        self.url = url.rstrip("/")
        self.remaining: int | None = None
        self._lock = _threading.Lock()
        self._known = False
        self._script: str | None = None

    def call(self, tool: str, arguments: dict[str, _Any]) -> _Any:
        script = self.script()
        status, body = self.send("/tools/" + _urlparse.quote(tool, safe=""), arguments, script)
        if status == 200 and isinstance(body, dict) and "status_code" in body:
            return self._outcome(tool, body)
        if status == 429:
            self.remaining = 0
            raise CapReached(status, body, tool)
        raise ToolError(status, body, tool)

    def script(self) -> str | None:
        """The running script's digest; its text sent to the bridge on the first call, once."""
        with self._lock:
            if not self._known:
                self._known = True
                found = running_script()
                if found is not None:
                    text, self._script = found
                    if len(text.encode("utf-8")) <= MAX_SCRIPT_BYTES:
                        # A text the bridge does not keep does not stop the script: its calls still
                        # carry its digest.
                        with _contextlib.suppress(ToolError):
                            self.send("/scripts", {"sha256": self._script, "text": text}, None)
            return self._script

    def send(self, path: str, payload: dict[str, _Any], script: str | None) -> tuple[int, _Any]:
        """One request to the bridge: its status and its body. A 503 (the tool server cannot tell yet
        whether the run is offered scripts) and a refused connection (the server is starting) are
        tried again, after `RETRY_DELAYS`."""
        data = _json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if script is not None:
            headers[SCRIPT_HEADER] = script
        delays = [*RETRY_DELAYS, None]
        for delay in delays:
            # `http` only (`__init__`): the bridge's own address, never a file or another scheme.
            request = _urlrequest.Request(self.url + path, data=data, headers=headers, method="POST")  # noqa: S310
            try:
                with _OPENER.open(request, timeout=TIMEOUT_S) as answer:
                    return int(answer.status), _decoded(answer.read())
            except _urlerror.HTTPError as error:
                status, body = int(error.code), _decoded(error.read())
                if status != 503 or delay is None:
                    return status, body
            except _urlerror.URLError as error:
                if not isinstance(error.reason, ConnectionRefusedError) or delay is None:
                    unreachable = {
                        "title": "Tool bridge unreachable",
                        "detail": f"{self.url}: {error.reason}",
                    }
                    raise ToolError(0, unreachable) from error
            _time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover - the last delay is None

    def _outcome(self, tool: str, answer: dict[str, _Any]) -> _Any:
        code, result = answer.get("status_code"), answer.get("result")
        left = answer.get("remaining")
        self.remaining = left if isinstance(left, int) and not isinstance(left, bool) else None
        suspicions = answer.get("suspicions")
        for reason in _reasons(suspicions):
            _warn(f"result of {tool} flagged as a suspected injection: {reason}")
        if not isinstance(code, int) or isinstance(code, bool):
            raise ToolError(200, answer, tool)
        if code == 451:
            raise Withheld(code, result, tool, suspicions)
        if code >= 400:
            raise ToolError(code, result, tool)
        if code == 202 and isinstance(result, dict) and result.get("action"):
            return Proposal(
                tool,
                str(result["action"]),
                str(result.get("status") or "unknown"),
                str(result.get("decision_path") or "the console"),
                str(result.get("message") or ""),
            )
        return unwrapped(tool, result)


def unwrapped(tool: str, result: _Any) -> _Any:
    """What a script reads of a result the platform served (the module's docstring has the rule)."""
    if not _is_mcp(result):
        return result
    if result.get("isError") is True:
        raise ToolError(200, result, tool)
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = "\n".join(
        block["text"]
        for block in result["content"]
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    cut = result.get("truncated")
    if cut is not None:
        sizes = (
            f" ({cut.get('returned_bytes')} of {cut.get('total_bytes')} bytes)"
            if isinstance(cut, dict)
            else ""
        )
        _warn(f"result of {tool} was cut by the platform{sizes}: ask for less (a filter, a page)")
    try:
        return _json.loads(text)
    except ValueError:
        return text


def running_script() -> tuple[str, str] | None:
    """The running script's text and its SHA-256: the `__main__` module's file, or the code of
    `python3 -c`; None for a process that runs no script file (standard input, an interactive
    session) or whose file is not UTF-8."""
    main = _sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    text: str | None = None
    if isinstance(path, str) and path:
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    else:
        text = _inline_code(list(getattr(_sys, "orig_argv", [])))
    if text is None:
        return None
    return text, _hashlib.sha256(text.encode("utf-8")).hexdigest()


def _inline_code(argv: list[str]) -> str | None:
    """The code of `python3 -c '<code>'`, as the interpreter received it (Python 3.10 and later)."""
    if "-c" in argv[1:-1]:
        return argv[argv.index("-c", 1) + 1]
    return None


def _bridge() -> _Bridge:
    with _LOCK:
        if "bridge" not in _STATE:
            url = _os.environ.get(BRIDGE_VARIABLE, "")
            if not url:
                raise RuntimeError(
                    f"{BRIDGE_VARIABLE} is not set: this process is not an agent's of a Choregos run "
                    "offered tool scripts"
                )
            _STATE["bridge"] = _Bridge(url)
        return _STATE["bridge"]


def _index() -> list[dict[str, _Any]]:
    """The run's tools, as the runner wrote them beside this file (`_index.json`)."""
    where = list(globals().get("__path__") or [_os.path.dirname(_os.path.abspath(__file__))])
    try:
        with open(_os.path.join(where[0], "_index.json"), encoding="utf-8") as handle:
            entries = _json.load(handle)
    except (OSError, ValueError):
        return []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _is_mcp(result: _Any) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("content"), list)
        and all(isinstance(block, dict) and "type" in block for block in result["content"])
    )


def _decoded(raw: bytes) -> _Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return _json.loads(text)
    except ValueError:
        return text


def _reasons(suspicions: _Any) -> list[str]:
    """The injection guard's reasons, each once: never the excerpts, which a third party wrote."""
    reasons: list[str] = []
    for suspicion in suspicions if isinstance(suspicions, list) else []:
        named = suspicion.get("motif") if isinstance(suspicion, dict) else suspicion
        reason = " ".join(str(named or "unnamed").split())[:100]
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _summary(body: _Any) -> str:
    """One line of an error answer: a problem's title and detail, an error's text, a tool's text."""
    if isinstance(body, dict):
        if "title" in body or "detail" in body:
            return " — ".join(str(body[key]) for key in ("title", "detail") if body.get(key))
        if body.get("error"):
            return str(body["error"])
        if _is_mcp(body):
            return " ".join(str(b.get("text", "")) for b in body["content"] if b.get("type") == "text")[:500]
    return (body if isinstance(body, str) else _json.dumps(body, default=str))[:500]


def _warn(line: str) -> None:
    _sys.stderr.write(f"{WARNING_PREFIX} {line}\n")
    _sys.stderr.flush()


__all__ = [
    "BRIDGE_VARIABLE",
    "DIRECTORY_VARIABLE",
    "CapReached",
    "Proposal",
    "ToolError",
    "Withheld",
    "call",
    "remaining",
    "search",
]
