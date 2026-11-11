#!/usr/bin/env python3
"""Smoke test of a native `choregos` binary, run where no Python is available (S62-07).

    python tools/cli_binary/smoke.py dist/cli/choregos-linux-x86_64 --expect-version 0.25.3
    python tools/cli_binary/smoke.py dist/cli/choregos-linux-x86_64 --expect-version 0.25.3 \\
        --container debian:bookworm-slim

This script is the DRIVER: it needs the standard library of any Python 3.12, writes the files the
binary reads, and serves a fake A2A door. The binary under test never sees a Python:

- natively, it runs with an environment built from nothing — a PATH that is an empty directory
  (`System32` alone on Windows), a fresh HOME, no `PYTHON*` variable but a `PYTHONHOME` and a
  `PYTHONPATH` that point nowhere — and the driver checks that no `python` is on that PATH;
- with `--container IMAGE` (Linux), the same checks run a second time inside a container whose
  image has no Python at all (the driver checks that too), reaching the door as
  `host.docker.internal` (Docker's `host-gateway`, on Docker Engine and Docker Desktop alike).

What it proves, in order: the size budget; `choregos --version` prints the expected version, and its
start-up stays under budget (a cold start unpacks the binary, a warm one reuses it: the warm median
is enforced, the cold one is printed); `workflow templates` lists the core's templates (the data
files are inside); `workflow validate` passes a shipped template and fails a broken workflow and an
unreadable YAML with exit code 1; `a2a skills` prints the skills of the fake door's card and its
fields, and a refused token exits 1. Any failure exits 1: the release and the CI stop there.

The budgets. Measured on the first builds (Linux x86_64, 2026-11-11, a 24-core machine): 24.8 MB,
`--version` in 0.69 s warm and cold alike; the CPython import of the CLI alone takes 0.30 s, the
onefile bootstrap's check of its unpacked files the rest. They leave room for the other targets
and for the dependencies to grow, not for a regression in kind:
- size: 40 MB. The binary carries a whole Python (its shared library alone is 31 MB unpacked),
  pydantic-core and the CLI's thousand modules compiled to C. Before S62-07 trimmed it, it was
  33 MB with 1,264 modules: zstandard, all of Pygments' 260 lexers; the OpenTelemetry SDK or the
  server coming back would cross the line.
- warm `--version`: 2.0 s. A person types it; three times the measure covers the slower runners
  (Windows, whose antivirus scans each new executable it maps; the Intel Mac). A CLI that imported
  the whole core at start-up again (0.5 s of imports instead of 0.3) stays under it: the import
  time is guarded by the Python tests, this budget catches a binary that unpacks itself each call.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "packages" / "core" / "src" / "choregos_core" / "dsl" / "templates"
MAX_SIZE_MB = 40.0
MAX_WARM_START_S = 2.0
#: The extension the CLI reads a skill's fields from (`choregos_cli.a2a.SKILL_FIELDS_EXTENSION`).
SKILL_FIELDS_EXTENSION = "https://choregos.dev/a2a/extensions/skill-fields/v1"
ORG = "smoke"
TOKEN = "smoke-a2a-token"  # noqa: S105 - the fake door's own, valid nowhere else
CARD: dict[str, Any] = {
    "name": "Choregos — smoke",
    "url": "",
    "version": "0",
    "skills": [
        {
            "id": "smoke/triage",
            "name": "triage",
            "description": "Triage a request",
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
            "tags": ["smoke"],
        },
        {
            "id": "smoke/release-notes",
            "name": "release-notes",
            "description": "Write the release notes",
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["text/markdown"],
            "tags": ["smoke"],
        },
    ],
    "capabilities": {
        "streaming": True,
        "extensions": [
            {
                "uri": SKILL_FIELDS_EXTENSION,
                "params": {
                    "skills": {
                        "smoke/triage": {
                            "type": "object",
                            "required": ["ticket"],
                            "properties": {
                                "ticket": {"type": "string"},
                                "priority": {"enum": ["low", "high"]},
                            },
                        }
                    }
                },
            }
        ],
    },
}
BROKEN_WORKFLOW = """\
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: broken
  version: 1
actors:
  dev: { type: agent, role: implement }
states:
  inbox: { display: Inbox }
  done: { display: Done, terminal: true }
transitions:
  - id: t-go
    from: inbox
    to: nowhere
    by: ghost
"""
UNREADABLE_YAML = "metadata: [1,\n"


class SmokeError(AssertionError):
    """One check of the smoke test did not hold."""


# ───────────────────────────── the fake door ─────────────────────────────


class _Door(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != f"/a2a/orgs/{ORG}/.well-known/agent-card.json":
            self._send(404, {"error": f"no such path: {self.path}"})
        elif self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "invalid token"})
        else:
            self._send(200, CARD)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: the smoke test prints its own lines."""


@contextlib.contextmanager
def fake_door(bind: str = "127.0.0.1") -> Iterator[int]:
    """A fake A2A door; yields its port. `0.0.0.0` only when a container must reach it."""
    server = ThreadingHTTPServer((bind, 0), _Door)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


# ───────────────────────────── running the binary ─────────────────────────────


@dataclass
class Result:
    code: int
    out: str
    err: str
    seconds: float

    @property
    def text(self) -> str:
        return self.out + self.err


def bare_environment(work: pathlib.Path, api_url: str) -> dict[str, str]:
    """An environment built from nothing: no PATH to a Python, a fresh home, no profile."""
    home = work / "home"
    home.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "CHOREGOS_CONFIG": str(work / "no-profile" / "config.json"),
        "CHOREGOS_API_URL": api_url,
        # Wide enough for Rich never to wrap a table cell the checks look for; no colour codes.
        "COLUMNS": "200",
        "TERM": "dumb",
        "NO_COLOR": "1",
        # Point nowhere: a binary that still looked for a Python's library would fail here.
        "PYTHONHOME": str(work / "no-python-home"),
        "PYTHONPATH": str(work / "no-python-path"),
    }
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        local = home / "AppData" / "Local"
        local.mkdir(parents=True, exist_ok=True)
        temp = work / "temp"
        temp.mkdir(exist_ok=True)
        env.update(
            {
                # Sockets need SYSTEMROOT. System32 only: the Python launcher `py.exe` may sit
                # in the Windows directory itself.
                "PATH": str(pathlib.Path(system_root) / "System32"),
                "SYSTEMROOT": system_root,
                "WINDIR": system_root,
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(local),
                "APPDATA": str(home / "AppData" / "Roaming"),
                "TEMP": str(temp),
                "TMP": str(temp),
            }
        )
    else:
        empty = work / "empty-path"
        empty.mkdir(exist_ok=True)
        env["PATH"] = str(empty)
        if os.environ.get("TMPDIR"):
            env["TMPDIR"] = os.environ["TMPDIR"]
    return env


def no_python_on(path: str) -> None:
    for name in ("python", "python3", "python3.12", "py"):
        found = shutil.which(name, path=path)
        if found:
            raise SmokeError(f"a Python is on the binary's PATH: {found}")


@dataclass
class Runner:
    """Runs the binary natively, or in a container whose image has no Python."""

    binary: pathlib.Path
    work: pathlib.Path
    env: dict[str, str]
    container: str | None = None
    log: list[str] = field(default_factory=list)

    def command(self, args: list[str]) -> list[str]:
        if self.container is None:
            return [str(self.binary), *args]
        docker = shutil.which("docker") or "docker"
        inside = {**self.env, "HOME": "/smoke/home", "CHOREGOS_CONFIG": "/smoke/no-profile/config.json"}
        inside["CHOREGOS_API_URL"] = self.env["CHOREGOS_API_URL"].replace("127.0.0.1", "host.docker.internal")
        inside.update(
            {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONHOME": "/nowhere", "PYTHONPATH": "/nowhere"}
        )
        user = [f"--user={os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
        return [
            docker,
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            *user,
            f"--volume={self.binary.resolve()}:/opt/choregos/choregos:ro",
            f"--volume={self.work}:/smoke",
            "--workdir=/smoke",
            *[f"--env={key}={value}" for key, value in sorted(inside.items())],
            self.container,
            "/opt/choregos/choregos",
            *args,
        ]

    def run(self, *args: str) -> Result:
        started = time.perf_counter()
        completed = subprocess.run(  # noqa: S603 - the binary under test, or docker
            self.command(list(args)),
            cwd=self.work,
            env=self.env if self.container is None else None,
            capture_output=True,
            timeout=300,
            check=False,
        )
        seconds = time.perf_counter() - started
        result = Result(
            completed.returncode,
            completed.stdout.decode("utf-8", "replace"),
            completed.stderr.decode("utf-8", "replace"),
            seconds,
        )
        self.log.append(f"$ choregos {' '.join(args)}  -> exit {result.code} in {seconds:.2f} s")
        return result


def expect(result: Result, code: int, *needles: str, what: str) -> None:
    if result.code != code:
        raise SmokeError(f"{what}: exit {result.code}, expected {code}\n{result.text}")
    missing = [n for n in needles if n not in result.text]
    if missing:
        raise SmokeError(f"{what}: {missing} not in the output\n{result.text}")


# ───────────────────────────── the checks ─────────────────────────────


def write_inputs(work: pathlib.Path) -> None:
    shutil.copy(TEMPLATES / "default-simple.yaml", work / "template.yaml")
    (work / "broken.yaml").write_text(BROKEN_WORKFLOW, encoding="utf-8")
    (work / "unreadable.yaml").write_text(UNREADABLE_YAML, encoding="utf-8")


def functional_checks(runner: Runner, version: str) -> None:
    expect(runner.run("--version"), 0, f"choregos {version}", what="--version")
    expect(runner.run("workflow", "templates"), 0, "default-simple", "incident-postmortem", what="templates")
    expect(
        runner.run("workflow", "validate", "template.yaml"), 0, "template.yaml is valid", what="a template"
    )
    expect(
        runner.run("workflow", "validate", "broken.yaml"),
        1,
        "unknown target state: nowhere",
        "unknown actor: ghost",
        what="a broken workflow",
    )
    expect(
        runner.run("workflow", "validate", "unreadable.yaml"), 1, "invalid workflow", what="unreadable YAML"
    )
    runner.env["CHOREGOS_A2A_TOKEN"] = TOKEN
    expect(
        runner.run("a2a", "skills", "--org", ORG),
        0,
        "smoke/triage",
        "smoke/release-notes",
        "ticket*",
        what="a2a skills",
    )
    runner.env["CHOREGOS_A2A_TOKEN"] = "not-the-token"  # noqa: S105 - refused on purpose
    expect(runner.run("a2a", "skills", "--org", ORG), 1, "HTTP 401", "invalid token", what="a refused token")
    del runner.env["CHOREGOS_A2A_TOKEN"]


def timings(runner: Runner, version: str, warm_runs: int = 5) -> tuple[float, float]:
    """(cold, warm median) seconds of `--version`: the first call unpacks, the next ones reuse."""
    cold = runner.run("--version")
    expect(cold, 0, f"choregos {version}", what="--version (cold)")
    warm = [runner.run("--version").seconds for _ in range(warm_runs)]
    return cold.seconds, statistics.median(warm)


def container_has_no_python(image: str) -> None:
    docker = shutil.which("docker") or "docker"
    probe = subprocess.run(  # noqa: S603
        [docker, "run", "--rm", "--entrypoint=/bin/sh", image, "-c", "command -v python3 python py || true"],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    if probe.stdout.strip():
        raise SmokeError(f"the image {image} has a Python: {probe.stdout.strip()}")


def smoke(
    binary: pathlib.Path,
    version: str,
    *,
    container: str | None = None,
    max_size_mb: float = MAX_SIZE_MB,
    max_warm_start_s: float = MAX_WARM_START_S,
) -> dict[str, Any]:
    if not binary.is_file():
        raise SmokeError(f"no binary at {binary}")
    size = binary.stat().st_size
    bind = "0.0.0.0" if container else "127.0.0.1"  # noqa: S104 - the container reaches it from outside
    with tempfile.TemporaryDirectory(prefix="choregos-smoke-") as tmp, fake_door(bind) as port:
        work = pathlib.Path(tmp).resolve()
        write_inputs(work)
        runner = Runner(binary, work, bare_environment(work, f"http://127.0.0.1:{port}"))
        no_python_on(runner.env["PATH"])
        cold, warm = timings(runner, version)
        functional_checks(runner, version)
        if container:
            container_has_no_python(container)
            in_container = Runner(binary, work, dict(runner.env), container=container)
            functional_checks(in_container, version)
            runner.log += [f"[{container}] {line}" for line in in_container.log]
    report = {
        "binary": binary.name,
        "version": version,
        "size_bytes": size,
        "size_mb": round(size / 1_000_000, 1),
        "cold_start_s": round(cold, 3),
        "warm_start_s": round(warm, 3),
        "budget": {"size_mb": max_size_mb, "warm_start_s": max_warm_start_s},
        "container": container,
        "log": runner.log,
    }
    if size / 1_000_000 > max_size_mb:
        raise SmokeError(f"{binary.name} is {size / 1_000_000:.1f} MB, over the {max_size_mb} MB budget")
    if warm > max_warm_start_s:
        raise SmokeError(f"`--version` takes {warm:.2f} s warm, over the {max_warm_start_s} s budget")
    return report


def _summary(report: dict[str, Any]) -> str:
    return (
        f"| `{report['binary']}` | {report['version']} "
        f"| {report['size_mb']} MB (≤ {report['budget']['size_mb']}) "
        f"| {report['cold_start_s']} s | {report['warm_start_s']} s (≤ {report['budget']['warm_start_s']}) "
        f"| {report['container'] or 'native only'} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test of a native choregos binary.")
    parser.add_argument("binary", type=pathlib.Path)
    parser.add_argument("--expect-version", required=True, help="the version `--version` must print")
    parser.add_argument("--container", help="also run the checks in this image (Linux, no Python in it)")
    parser.add_argument("--max-size-mb", type=float, default=MAX_SIZE_MB)
    parser.add_argument("--max-warm-start-s", type=float, default=MAX_WARM_START_S)
    parser.add_argument("--report", type=pathlib.Path, help="write the measures there, as JSON")
    args = parser.parse_args()
    try:
        report = smoke(
            args.binary.resolve(),
            args.expect_version,
            container=args.container,
            max_size_mb=args.max_size_mb,
            max_warm_start_s=args.max_warm_start_s,
        )
    except SmokeError as failure:
        print(f"SMOKE FAILED: {failure}", file=sys.stderr)
        return 1
    print("\n".join(report["log"]))
    print(
        f"OK {report['binary']} {report['version']}: {report['size_mb']} MB, "
        f"--version {report['cold_start_s']} s cold, {report['warm_start_s']} s warm"
    )
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("| binary | version | size | cold start | warm start | container |\n")
            fh.write("|---|---|---|---|---|---|\n" + _summary(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
