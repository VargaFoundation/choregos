#!/usr/bin/env python3
"""Fake ACP agent: plays the complete protocol, scripted by an environment variable.

It serves the conformance suite and the runner's tests: it answers `initialize`,
opens a session, asks for permissions, writes files, and produces a
`.choregos/result.json`. Its scenario is given by `FAKE_AGENT_SCRIPT` (JSON).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return dict(json.loads(line))
    except json.JSONDecodeError:
        return {}


def request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Send a request to the client (runner) and wait for its answer."""
    send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    while True:
        message = read()
        if message is None:
            return None
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message


def _load(message: dict[str, Any], script: dict[str, Any]) -> None:
    """A resumed conversation (S35-03): refused when the script says so, otherwise the history
    is replayed as one update, then the load answers null."""
    params = message.get("params") or {}
    if script.get("load_fails"):
        send(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32002, "message": "unknown session"},
            }
        )
        return
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": params.get("sessionId"),
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": f"history of {params.get('sessionId')}"},
                },
            },
        }
    )
    send({"jsonrpc": "2.0", "id": message.get("id"), "result": None})


def _initialize(message_id: Any, script: dict[str, Any]) -> bool:
    """Answer `initialize`; False when the script asks the agent to fail it."""
    if script.get("fail_initialize"):
        send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": "broken"}})
        return False
    send(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {
                    "loadSession": bool(script.get("load_session")),
                    "promptCapabilities": {"image": False},
                },
                "agentInfo": {"name": "fake-agent", "version": "1.0.0"},
            },
        }
    )
    return True


def _call_model(call: dict[str, Any]) -> None:
    """Calls the model the way a backend does: the base URL and the key from its environment.
    The answer goes to stderr; a failure too, the turn goes on."""
    import urllib.request

    url = os.environ[call["base_env"]].rstrip("/") + call["path"]
    body = json.dumps(call.get("body", {})).encode()
    headers = {"content-type": "application/json", "x-api-key": os.environ.get(call["key_env"], "")}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=10) as answer:
            sys.stderr.write(f"model call {url}: {answer.status}\n")
    except OSError as exc:
        sys.stderr.write(f"model call {url}: {exc}\n")


def _unasked(actions: list[dict[str, Any]]) -> None:
    """`unasked`: what a shell command would do without asking any permission (ADR 0094) — write a
    file anywhere, connect to a TCP port. The outcomes go to `$HOME/unasked.json`, in order."""
    if not actions:
        return
    import socket

    outcomes: list[str] = []
    for action in actions:
        try:
            if "write" in action:
                Path(action["write"]).write_text("unasked\n", encoding="utf-8")
            else:
                with socket.create_connection(("127.0.0.1", int(action["connect"])), timeout=5):
                    pass
            outcomes.append("ok")
        except OSError as exc:
            outcomes.append(type(exc).__name__)
    (Path(os.environ["HOME"]) / "unasked.json").write_text(json.dumps(outcomes), encoding="utf-8")


def _http(calls: list[dict[str, Any]]) -> None:
    """`http`: what a script the agent wrote would send to the run's tool server (the tool bridge,
    ADR 0122), with no credential of its own. Each answer (status, body) goes to `$HOME/http.json`,
    in order, beside the run variables the agent's process holds."""
    if not calls:
        return
    import urllib.error
    import urllib.request

    answers: list[dict[str, Any]] = []
    for call in calls:
        method = call.get("method", "POST")
        request = urllib.request.Request(
            call["url"],
            data=json.dumps(call.get("body", {})).encode() if method == "POST" else None,
            method=method,
            headers={"content-type": "application/json", **call.get("headers", {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                answers.append({"status": answer.status, "body": answer.read().decode()})
        except urllib.error.HTTPError as error:
            answers.append({"status": error.code, "body": error.read().decode()})
        except OSError as exc:
            answers.append({"status": None, "body": type(exc).__name__})
    held = sorted(name for name in os.environ if name.startswith("CHOREGOS_RUN_"))
    (Path(os.environ["HOME"]) / "http.json").write_text(
        json.dumps({"answers": answers, "run_variables": held}), encoding="utf-8"
    )


#: What `scripts` records of the agent's environment: what the runner sets for tool scripts (ADR 0122).
SCRIPT_VARIABLES = ("CHOREGOS_TOOLS_DIR", "CHOREGOS_TOOLS_BRIDGE", "PYTHONPATH", "TMPDIR")


def _scripts(scripts: list[dict[str, Any]], workspace: Path) -> None:
    """`scripts`: what an agent does with a script it wrote (ADR 0122) — writes it from its shell under
    `$TMPDIR`, then runs the file with Python (`python`, `[sys.executable]` by default) and its own
    environment, in the workspace. Each run's exit code, stdout and stderr go to `$HOME/scripts.json`,
    in order, beside the variables the runner sets for tool scripts."""
    if not scripts:
        return
    import tempfile

    ran: list[dict[str, Any]] = []
    for script in scripts:
        path = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / script["name"]
        try:
            path.write_text(script["text"], encoding="utf-8")
        except OSError as exc:  # said, not crashed: a dead agent would leave its turn unanswered
            ran.append({"code": None, "stdout": "", "stderr": repr(exc)})
            continue
        completed = subprocess.run(
            [*script.get("python", [sys.executable]), str(path)],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        ran.append({"code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
    (Path(os.environ["HOME"]) / "scripts.json").write_text(
        json.dumps({"runs": ran, "env": {name: os.environ.get(name) for name in SCRIPT_VARIABLES}}),
        encoding="utf-8",
    )


def _say(session_id: str, notes: list[str]) -> None:
    for note in notes:
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": note},
                    },
                },
            }
        )


def main() -> int:
    script: dict[str, Any] = json.loads(os.environ.get("FAKE_AGENT_SCRIPT", "{}"))
    workspace = Path(os.environ.get("CHOREGOS_WORKSPACE_PATH", ".")).resolve()
    outgoing_id = 1000
    prompts_seen = 0

    while True:
        message = read()
        if message is None:
            return 0
        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            if not _initialize(message_id, script):
                return 3
            continue

        if method == "session/new":
            send({"jsonrpc": "2.0", "id": message_id, "result": {"sessionId": "sess-fake-1"}})
            continue

        if method == "session/load":
            _load(message, script)
            continue

        if method == "session/prompt":
            prompts_seen += 1
            plan = _plan_for(script, prompts_seen)
            session_id = (message.get("params") or {}).get("sessionId", "sess-fake-1")

            _say(session_id, plan.get("messages", []))
            for call in plan.get("model_calls", []):
                _call_model(call)

            for write in plan.get("writes", []):
                outgoing_id += 1
                response = request(
                    outgoing_id,
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": f"tc-{outgoing_id}",
                            "title": f"write {write['path']}",
                            "kind": "edit",
                            "rawInput": {"path": write["path"]},
                        },
                        "options": [
                            {"optionId": "allow_once", "name": "Autoriser", "kind": "allow_once"},
                            {"optionId": "reject_once", "name": "Refuser", "kind": "reject_once"},
                        ],
                    },
                )
                allowed = _is_allowed(response)
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": f"tc-{outgoing_id}",
                                "status": "completed" if allowed else "failed",
                            },
                        },
                    }
                )
                if allowed:
                    target = workspace / write["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write(target, write)

            outgoing_id = _report_tool_calls(session_id, plan.get("tool_calls", []), outgoing_id)
            outgoing_id = _shell(session_id, plan.get("shell", []), workspace, outgoing_id)
            _unasked(plan.get("unasked", []))
            _http(plan.get("http", []))
            _scripts(plan.get("scripts", []), workspace)

            for command in plan.get("commands", []):
                outgoing_id += 1
                request(
                    outgoing_id,
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {
                            "toolCallId": f"tc-{outgoing_id}",
                            "title": command,
                            "kind": "execute",
                            "rawInput": {"command": command},
                        },
                        "options": [],
                    },
                )

            _write_result(plan, workspace)

            _end_turn(plan, message_id)
            continue

        if method == "session/cancel":
            return 0

        if message_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"unknown method: {method}"},
                }
            )
    return 0


def _report_tool_calls(session_id: str, calls: list[dict[str, Any]], outgoing_id: int) -> int:
    """`tool_calls`: calls reported the way opencode does (S22-44) — announced `pending` with an
    empty `rawInput` (or `pending_input`: opencode's shell calls carry their `cwd` already, #611),
    the arguments on the `in_progress` update, then `completed`."""
    for call in calls:
        outgoing_id += 1
        call_id = f"tc-{outgoing_id}"
        announced = {"toolCallId": call_id, "title": call["tool"], "kind": call["kind"]}
        for update in (
            {
                "sessionUpdate": "tool_call",
                **announced,
                "status": "pending",
                "rawInput": call.get("pending_input", {}),
            },
            {
                "sessionUpdate": "tool_call_update",
                **announced,
                "status": "in_progress",
                "rawInput": call["input"],
            },
            {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed"},
        ):
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": session_id, "update": update},
                }
            )
    return outgoing_id


def _shell(session_id: str, commands: list[list[str]], workspace: Path, outgoing_id: int) -> int:
    """`shell`: commands an agent's own shell tool runs, without asking (ADR 0094), reported the way a
    shell tool's call is: announced, then its output — stdout and stderr together — as the call's
    content, and its exit code, `failed` when it is not 0."""
    for command in commands:
        outgoing_id += 1
        call_id = f"tc-{outgoing_id}"
        announced = {"toolCallId": call_id, "title": " ".join(command), "kind": "execute"}
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call",
                        **announced,
                        "status": "pending",
                        "rawInput": {"command": " ".join(command)},
                    },
                },
            }
        )
        completed = subprocess.run(
            command, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": call_id,
                        "status": "completed" if completed.returncode == 0 else "failed",
                        "content": [
                            {"type": "content", "content": {"type": "text", "text": completed.stdout}}
                        ],
                        "rawOutput": {"exit_code": completed.returncode},
                    },
                },
            }
        )
    return outgoing_id


def _write(target: Path, write: dict[str, Any]) -> None:
    """Text by `content`, or bytes by `b64` — a binary deliverable: a deck, an image."""
    if "b64" in write:
        target.write_bytes(base64.b64decode(write["b64"]))
    else:
        target.write_text(write.get("content", ""), encoding="utf-8")


def _write_result(plan: dict[str, Any], workspace: Path) -> None:
    """Write the scripted `result.json`, with what the agent `reads` hashed into `outputs.seen` and
    what its `greps` found in place of `{grep}`."""
    # `reads`: files the agent opens; their sha256 (or null when absent) go into the result's
    # `outputs.seen`, so a test can tell what the agent actually found in its workspace.
    seen = {
        path: (
            "sha256:" + hashlib.sha256((workspace / path).read_bytes()).hexdigest()
            if (workspace / path).is_file()
            else None
        )
        for path in plan.get("reads", [])
    }
    # `greps`: really searches the workspace (the knowledge corpus, ADR 0051); what it finds
    # replaces `{grep}` in the result summary.
    found = _grep(plan, workspace)
    if "result" in plan:
        result_path = workspace / ".choregos" / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        content = _decorate(plan["result"], seen, found)
        result_path.write_text(
            content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _decorate(content: Any, seen: dict[str, Any], found: str) -> Any:
    """The scripted result, with what the agent read (`seen`) and found (`{grep}`) put in."""
    if seen and isinstance(content, dict):
        content = {**content, "outputs": {**content.get("outputs", {}), "seen": seen}}
    if isinstance(content, dict) and "{grep}" in str(content.get("summary", "")):
        content = {**content, "summary": content["summary"].replace("{grep}", found.strip())}
    return content


def _grep(plan: dict[str, Any], workspace: Path) -> str:
    """Runs the plan's `greps` for real in the workspace and returns what they found."""
    found = ""
    for grep in plan.get("greps", []):
        completed = subprocess.run(
            ["grep", "-rn", str(grep["pattern"]), str(grep.get("path", "."))],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        found += completed.stdout
    # `runs`: run for real in the workspace with the agent's own environment, as an agent's
    # shell tool does (`git status`); their stdout and stderr go into `{grep}` too.
    for command in plan.get("runs", []):
        completed = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
        found += completed.stdout + completed.stderr
    return found


def _end_turn(plan: dict[str, Any], message_id: Any) -> None:
    """Ends the prompt turn: its stop reason — or an error, as an agent does when its provider
    refuses the call (`error`). `stderr` lines are written first, as a provider's message would be."""
    for line in plan.get("stderr", []):
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    if "error" in plan:
        send({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32603, "message": plan["error"]}})
        return
    send(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {"stopReason": plan.get("stop_reason", "end_turn")},
        }
    )


def _plan_for(script: dict[str, Any], prompt_index: int) -> dict[str, Any]:
    turns: list[dict[str, Any]] = script.get("turns", [])
    if not turns:
        return {}
    index = min(prompt_index, len(turns)) - 1
    return turns[index]


def _is_allowed(response: dict[str, Any] | None) -> bool:
    if response is None or "result" not in response:
        return False
    outcome = (response["result"] or {}).get("outcome") or {}
    return str(outcome.get("optionId", "")).startswith("allow")


if __name__ == "__main__":
    raise SystemExit(main())
