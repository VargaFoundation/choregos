"""The runner's side of the agent-shape measurement (S65-04a, ADR 0146).

Run inside the RUNNER's container of a two-container pod made with Docker
(`tests/live/test_agent_shape_live.py`): the agent's container shares its network namespace, runs
`choregos-runner launcher` as another uid with a read-only root and holds no secret; both share
`/workspace` and `/exchange`. This script runs ONE real ACP step through `Runner.execute` with
`agent_isolation=container`, so the harness is started by the launcher and reaches its model on 4001
(the real gateway behind it), its credentialed MCP server on 4002 and the network on 4003 — the
runner's code, unchanged, with three test doubles only: the platform's internal API (in memory), the
MCP server (an `httpx` transport that wants its bearer header) and the pod's egress proxy (a container
beside the pod, `egress_proxy.py`).

What it records, in one JSON file per harness (`--out`): the runner's outcome and result; every request
the harness made of the model listener (method, path, query names, header names, status); the MCP
calls that reached the server and with which header; the tunnels the CONNECT listener opened or refused;
the project's checks (which ran in the agent's container) and what the probe found there; the key's
spend at the gateway before and after; the runner container's peak memory and CPU time. Secret values
never reach the file: every known one is replaced before it is written.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import resource
import secrets
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response
from tranibor_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    LaunchSpec,
    McpServerRef,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    StageInput,
    StageResult,
    TransitionRef,
    WorkItemRef,
)
from tranibor_contracts.stage import ToolsRef
from tranibor_core.hosted import LOOPBACK_KEY
from tranibor_runner import runner as runner_module
from tranibor_runner.config import RunnerSettings
from tranibor_runner.loopback.model import ModelProxy
from tranibor_runner.runner import Runner

HERE = Path(__file__).resolve().parent
CODE_WORD = "HELIOTROPE"
TASK = """\
Two small things, in this repository:

1. `calc.py`: `add(a, b)` returns `a - b`. Make it return the sum. `make test` must pass.
2. The MCP server `crm` has a tool `lookup`. Call it with `{"id": "42"}` and write the code word it
   returns, alone on one line, into `answer.txt`.

Do not change any other file. Keep it short: no exploration beyond these two files.
"""

# ──────────────────────────── the platform's internal API, in memory ────────────────────────────


class Relayed:
    """What `tranibor_tools_mcp.client.Relayed` is: the run's tool server relays nothing here."""

    def __init__(self, status_code: int, content: bytes, content_type: str) -> None:
        self.status_code, self.content, self.content_type = status_code, content, content_type


class InternalApi:
    """The internal API the runner calls, answering in memory and keeping what it is sent."""

    def __init__(self, stage_input: StageInput) -> None:
        self.stage_input = stage_input
        self.events: list[dict[str, Any]] = []
        self.results: list[StageResult] = []

    async def fetch_input(self) -> StageInput:
        return self.stage_input

    async def fetch_context(self) -> ContextPack:
        return ContextPack.empty(self.stage_input.work_item.title)

    async def fetch_ticket(self) -> dict[str, Any]:
        item = self.stage_input.work_item
        return {"key": item.key, "title": item.title, "body": item.body or ""}

    async def fetch_ci_logs(self, tail: int = 500) -> str:
        return ""

    async def fetch_skills(self) -> list[dict[str, Any]]:
        return []

    async def fetch_knowledge(self) -> list[dict[str, Any]]:
        return []

    async def fetch_inputs(self) -> list[dict[str, Any]]:
        return []

    async def fetch_input_content(self, name: str) -> bytes:
        raise KeyError(name)

    async def put_output(self, name: str, type_: str, data: bytes, digest: str) -> dict[str, Any]:
        return {"name": name, "type": type_, "digest": digest, "size": len(data), "version": 1}

    async def fetch_git_token(self) -> str | None:
        return None

    async def fetch_egress_token(self) -> str:
        return os.environ["CHOREGOS_RUN_S65A_EGRESS"]

    async def relay_tools(self) -> Relayed:
        return Relayed(200, b'{"tools": []}', "application/json")

    async def relay_tool_call(
        self, name: str, arguments: bytes, *, script: str | None, within_s: float
    ) -> Relayed:
        return Relayed(404, b'{"error": "no run tool here"}', "application/json")

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        self.events.extend(events)
        return len(events)

    async def post_result(self, result: StageResult) -> dict[str, Any]:
        self.results.append(result)
        return {"status": "recorded"}

    async def report_finding(self, finding: Any) -> dict[str, Any]:
        return {"accepted": True, "finding_id": "f1", "remaining": 0}

    async def request_scope_change(self, paths: list[str], justification: str) -> dict[str, Any]:
        return {"decision": "refused", "allowed_paths": []}

    async def ask_human(self, text: str, options: list[str] | None = None) -> dict[str, Any]:
        return {"status": "accepted"}

    async def aclose(self) -> None:
        return None


# ──────────────────────────── the MCP server, behind its header ────────────────────────────


class Crm:
    """A streamable-HTTP MCP server with one tool, that wants `Authorization: Bearer <token>`."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.requests: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        authorized = request.headers.get("authorization") == f"Bearer {self.token}"
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        method = body.get("method", "") if isinstance(body, dict) else "batch"
        self.requests.append({"http": request.method, "method": method, "authorized": authorized})
        if not authorized:
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.method != "POST":
            return httpx.Response(405)
        if not isinstance(body, dict) or body.get("id") is None:
            return httpx.Response(202)
        params = body.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "crm", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Looks a customer record up by its id and returns its code word.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            self.calls.append(params)
            record = str((params.get("arguments") or {}).get("id", "?"))
            result = {"content": [{"type": "text", "text": f"record {record}: code word {CODE_WORD}"}]}
        elif method == "ping":
            result = {}
        else:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "unknown"}},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, headers={"mcp-session-id": "s1"}
        )


# ──────────────────────────── what the harness asks of the model listener ────────────────────────────

MODEL_REQUESTS: list[dict[str, Any]] = []


class RecordingModelProxy(ModelProxy):
    """The runner's model listener, unchanged, that also notes each request: what the harness asked
    (paths, query and header NAMES, never values) and what it was answered."""

    async def handle(self, request: Request) -> Response:
        started = time.monotonic()
        sent = await request.body()  # Starlette keeps it for the listener
        answer = await super().handle(request)
        entry: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "query": sorted(request.query_params.keys()),
            "headers": sorted(request.headers.keys()),
            "status": answer.status_code,
            "seconds_to_headers": round(time.monotonic() - started, 2),
        }
        if answer.status_code >= 400:
            # What was refused, and why: the answer's text and the shape of what was sent (its keys and
            # the kinds of its parts), never the prompt itself.
            entry["answer"] = bytes(answer.body or b"")[:600].decode(errors="replace")
            entry["sent_shape"] = shape(sent)
        MODEL_REQUESTS.append(entry)
        return answer


def shape(raw: bytes) -> Any:
    """A request body's structure: the keys of its objects, the kinds of the parts of its messages."""
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return "not JSON"

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "…"
        if isinstance(node, dict):
            return {k: walk(v, depth + 1) for k, v in list(node.items())[:12] if k not in ("text", "content")}
        if isinstance(node, list):
            return [walk(item, depth + 1) for item in node[-3:]]
        return type(node).__name__

    return walk(body)


# ──────────────────────────── the project's checks, as the runner saw them ────────────────────────────

CHECKS: list[dict[str, Any]] = []
_run_checks = runner_module.run_checks


async def recording_checks(*args: Any, **kwargs: Any) -> Any:
    report = await _run_checks(*args, **kwargs)
    CHECKS.append(
        {
            "launcher": kwargs.get("launcher") is not None,
            "checks": [
                {"name": c.name, "ok": c.ok, "skipped": c.skipped, "output": (c.output or "")[-1500:]}
                for c in report.checks
            ],
        }
    )
    return report


# ──────────────────────────── the repository ────────────────────────────


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def repository(root: Path, digests: dict[str, str]) -> Path:
    """A bare origin the runner alone reaches, holding a small Python project with three checks: its
    tests, a dependency install through the proxy variables, and the probe."""
    origin, seed = root / "origin", root / "seed"
    shutil.rmtree(root, ignore_errors=True)
    origin.mkdir(parents=True)
    git("init", "--bare", "--initial-branch=main", cwd=origin)
    seed.mkdir()
    git("init", "--initial-branch=main", cwd=seed)
    git("config", "user.email", "seed@s65a", cwd=seed)
    git("config", "user.name", "seed", cwd=seed)
    (seed / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (seed / "test_calc.py").write_text(
        "import unittest\n\nfrom calc import add\n\n\n"
        "class AddTest(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n",
        encoding="utf-8",
    )
    (seed / "tools").mkdir()
    shutil.copy(HERE / "probe.py", seed / "tools" / "probe.py")
    shutil.copy(HERE / "deps.py", seed / "tools" / "deps.py")
    probe_args = " ".join(f"{label}={digest}" for label, digest in digests.items())
    (seed / "Makefile").write_text(
        "test:\n\tpython3 -m unittest -q test_calc\n"
        "deps:\n\tpython3 tools/deps.py --out $${TMPDIR:-/tmp}/s65a-deps.json\n"
        "probe:\n\tpython3 tools/probe.py --out $${TMPDIR:-/tmp}/s65a-probe.json"
        f" --loopback-key {LOOPBACK_KEY} {probe_args}\n",
        encoding="utf-8",
    )
    (seed / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "init", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-u", "origin", "main", cwd=seed)
    return origin


# ──────────────────────────── the step ────────────────────────────


def step(backend: str, origin: Path, crm_token: str) -> StageInput:
    gateway = os.environ["S65A_GATEWAY_URL"].rstrip("/")
    model = os.environ.get("S65A_MODEL", "choregos-claude-haiku")
    return StageInput(
        run_id=f"s65a-{backend}",
        attempt=1,
        project=ProjectRef(
            slug="shape",
            org="s65a",
            test_command="make test",
            lint_command="make deps",
            typecheck_command="make probe",
        ),
        work_item=WorkItemRef(key="s65a/shape#1", title="Fix add() and record the CRM code word", body=TASK),
        transition=TransitionRef(id="t-implement", role="implement", **{"from": "ready"}, to="in_progress"),
        repo=RepoRef(
            url=str(origin), base_branch="main", work_branch=f"choregos/s65a-{backend}", clone_depth=10
        ),
        agent=AgentRef(
            backend=backend, launch=LaunchSpec(env=json.loads(os.environ.get("S65A_LAUNCH_ENV", "{}")))
        ),
        model=ModelRef(
            litellm_model=model,
            base_url=gateway,
            api_format="anthropic" if backend == "claude-code" else "openai",
            # A gateway alias (`platform/standard`) says nothing of its family, which claude-code checks.
            provider_model=os.environ.get("S65A_PROVIDER_MODEL") or None,
        ),
        gateway_key="",  # the run's Secret carries it (`CHOREGOS_RUN_GATEWAY_KEY`), as on a cluster
        budget=Budget(usd=0.5, max_turns=int(os.environ.get("S65A_MAX_TURNS", "25")), max_minutes=8),
        allowed_paths=["calc.py", "answer.txt"],
        playbook=PlaybookRef(ref="implement@s65a", prompt="You implement the work item, nothing more."),
        tools=ToolsRef(
            mcp={
                "crm": McpServerRef(
                    url="https://crm.s65a.test/mcp", env={"Authorization": f"Bearer {crm_token}"}
                )
            }
        ),
        permissions=Permissions(
            write_paths=["calc.py", "answer.txt"],
            allow_domains=["crm.s65a.test", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"],
            dod_iterations=1,
            max_findings=1,
        ),
        callbacks=Callbacks(
            api_url="http://127.0.0.1:9/internal", run_token=os.environ["CHOREGOS_RUN_TOKEN"]
        ),
    )


# ──────────────────────────── the measurement ────────────────────────────


def cgroup() -> dict[str, Any]:
    """The runner container's own cgroup: its peak memory and CPU time, children included."""
    out: dict[str, Any] = {}
    root = Path("/sys/fs/cgroup")
    try:
        out["memory_peak_mib"] = round(int((root / "memory.peak").read_text()) / 2**20, 1)
    except (OSError, ValueError):
        out["memory_peak_mib"] = None
    try:
        stat = dict(line.split() for line in (root / "cpu.stat").read_text().splitlines())
        out["cpu_seconds"] = round(int(stat["usage_usec"]) / 1e6, 2)
    except (OSError, ValueError, KeyError):
        out["cpu_seconds"] = None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    out["runner_process_maxrss_mib"] = round(usage.ru_maxrss / 1024, 1)
    return out


async def spend(key: str) -> float | None:
    """The key's spend at the gateway, which books it a moment after the answer: three tries."""
    gateway = os.environ["S65A_GATEWAY_URL"].rstrip("/")
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                answer = await http.get(f"{gateway}/key/info", headers={"authorization": f"Bearer {key}"})
            return float(answer.json()["info"]["spend"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            await asyncio.sleep(2 + 3 * attempt)
    return None


def summarise(events: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(str(e.get("type")) for e in events)
    titles = [
        str(((e.get("payload") or {}).get("update") or {}).get("title", ""))[:80]
        for e in events
        if e.get("type") == "session/update"
        and ((e.get("payload") or {}).get("update") or {}).get("sessionUpdate") == "tool_call"
    ]
    picked = {
        "agent.isolation",
        "agent.confinement",
        "hosted.egress",
        "hosted.egress_refused",
        "hosted.egress_failed",
        "hosted.mcp_call",
        "hosted.mcp_refused",
        "session/request_permission",
        "dod.check",
        "dod.retry",
        "run.failed",
        "agent.stderr",
        "agent.exit",
        "turn.ended",
        "guard.stopped",
        "gateway.budget",
    }
    kept = [
        {"type": e.get("type"), "payload": e.get("payload")}
        for e in events
        if e.get("type") in picked or str(e.get("type", "")).startswith(("hosted.", "dod.", "agent."))
    ]
    return {"event_types": dict(types), "tool_calls": len(titles), "tool_titles": titles, "events": kept}


def scrub(text: str, values: list[str]) -> str:
    for value in values:
        if value:
            text = text.replace(value, "<redacted>")
    return text


class Escaped:
    """An exception that escaped `Runner.execute`, shaped as its outcome."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        self.exit_code = Escaped.Code()

    class Code(int):
        name = "ESCAPED_THE_RUNNER"

        def __new__(cls) -> Escaped.Code:
            return int.__new__(cls, -1)


def agent_logs(home: Path) -> dict[str, str]:
    """The tail of every log the harness wrote under its home (which the shared volume holds)."""
    logs: dict[str, str] = {}
    for path in sorted(home.rglob("*.log"))[:10] if home.is_dir() else []:
        try:
            logs[str(path.relative_to(home))] = path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError as exc:
            logs[str(path.relative_to(home))] = f"unreadable: {exc.strerror}"
    return logs


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def measure(backend: str, out: Path) -> int:
    key = os.environ["CHOREGOS_RUN_GATEWAY_KEY"]
    crm_token = secrets.token_hex(16)
    secret_values = {
        "gateway-key": key,
        "egress-token": os.environ["CHOREGOS_RUN_S65A_EGRESS"],
        "mcp-header": crm_token,
        "run-token": os.environ["CHOREGOS_RUN_TOKEN"],
    }
    digests = {label: hashlib.sha256(value.encode()).hexdigest() for label, value in secret_values.items()}
    origin = repository(Path(os.environ.get("S65A_DATA", "/var/tmp/s65a")), digests)
    stage_input = step(backend, origin, crm_token)
    api = InternalApi(stage_input)
    crm = Crm(crm_token)
    runner_module.ModelProxy = RecordingModelProxy  # type: ignore[misc]
    runner_module.run_checks = recording_checks  # type: ignore[assignment]
    runner = Runner(RunnerSettings(), api)  # type: ignore[arg-type]
    runner.mcp_transport = httpx.MockTransport(crm)
    exchange = Path(os.environ.get("TMPDIR", "/tmp"))
    for name in ("s65a-probe.json", "s65a-deps.json"):
        (exchange / name).unlink(missing_ok=True)

    before = await spend(key)
    started = time.monotonic()
    try:
        outcome = await runner.execute(
            stage_input.model_copy(update={"gateway_key": runner.settings.gateway_key}),
            api,  # type: ignore[arg-type]
        )
    except Exception as exc:
        outcome = Escaped(f"{type(exc).__name__}: {exc}")
    seconds = round(time.monotonic() - started, 1)
    await asyncio.sleep(3)  # the gateway books the spend after the answer
    after = await spend(key)

    workspace = runner.settings.workspace
    result = api.results[-1] if api.results else None
    report = {
        "backend": backend,
        "model": stage_input.model.litellm_model,
        "seconds": seconds,
        "exit_code": f"{outcome.exit_code.name} ({int(outcome.exit_code)})",
        "detail": outcome.detail,
        "result": result.model_dump(mode="json") if result else None,
        "files": {
            name: (workspace / name).read_text(encoding="utf-8")[:400]
            if (workspace / name).is_file()
            else None
            for name in ("calc.py", "answer.txt")
        },
        "model_requests": MODEL_REQUESTS,
        "mcp": {"requests": crm.requests, "calls": crm.calls},
        "checks": CHECKS,
        "tree": subprocess.run(
            ["ls", "-la", str(workspace)], capture_output=True, text=True, check=False
        ).stdout[-2000:],
        "agent_logs": agent_logs(exchange / f"choregos-{stage_input.run_id}"),
        "probe": read_json(exchange / "s65a-probe.json"),
        "deps": read_json(exchange / "s65a-deps.json"),
        "spend_usd": None if before is None or after is None else round(after - before, 6),
        "runner_container": cgroup(),
        **summarise(api.events),
    }
    text = scrub(json.dumps(report, indent=1, default=str), list(secret_values.values()))
    out.write_text(text, encoding="utf-8")
    print(f"{backend}: {report['exit_code']} in {seconds}s, spend {report['spend_usd']}", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(measure(args.backend, args.out)))


if __name__ == "__main__":
    main()
