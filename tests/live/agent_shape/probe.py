"""What the agent's container holds, seen from inside it (S65-04a): run as the project's check.

Standard library only. Given the SHA-256 digests of the run's credentials (`label=digest`), it looks for
them, never for the values, in every `/proc/<pid>/environ` and `/proc/<pid>/cmdline` it can read and in
every small file under the shared volumes, token by token; it lists the processes it sees, the model
and proxy variables of the harness's process, and whether a direct connection out succeeds where a
connection through the proxy variables does. It writes one JSON report to `--out`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import time
import urllib.request
from pathlib import Path

TOKEN = re.compile(rb"[A-Za-z0-9._~+/=-]{12,}")
SHOWN = (
    "ANTHROPIC_BASE_URL",
    "OPENAI_BASE_URL",
    "GOOGLE_GEMINI_BASE_URL",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "HOME",
    "TMPDIR",
)
KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY")


def found_in(data: bytes, digests: dict[str, str]) -> set[str]:
    wanted = {digest: label for label, digest in digests.items()}
    hits = set()
    for token in TOKEN.findall(data):
        label = wanted.get(hashlib.sha256(token).hexdigest())
        if label:
            hits.add(label)
    return hits


def processes(digests: dict[str, str], loopback_key: str) -> tuple[list[dict[str, object]], set[str]]:
    seen: list[dict[str, object]] = []
    hits: set[str] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        record: dict[str, object] = {"pid": int(entry.name)}
        try:
            record["uid"] = entry.stat().st_uid
            cmdline = (entry / "cmdline").read_bytes()
            record["cmd"] = cmdline.replace(b"\0", b" ").decode(errors="replace")[:160]
            hits |= found_in(cmdline.replace(b"\0", b" "), digests)
        except OSError:
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError as exc:
            record["environ"] = f"unreadable: {exc.strerror}"
        else:
            hits |= found_in(environ.replace(b"\0", b" "), digests)
            variables = dict(
                item.split("=", 1) for item in environ.decode(errors="replace").split("\0") if "=" in item
            )
            record["env"] = {name: variables[name] for name in SHOWN if name in variables}
            record["keys"] = {
                name: ("loopback" if variables[name] == loopback_key else "OTHER")
                for name in KEYS
                if name in variables
            }
        seen.append(record)
    return seen, hits


def files(roots: list[str], digests: dict[str, str]) -> set[str]:
    hits: set[str] = set()
    for root in roots:
        for path in Path(root).rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
                    continue
                if found := found_in(path.read_bytes(), digests):
                    hits |= {f"{label}@{path}" for label in found}
            except OSError:
                continue
    return hits


def egress() -> dict[str, object]:
    report: dict[str, object] = {}
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        report["direct"] = "connected"
    except OSError as exc:
        report["direct"] = f"refused: {exc}"[:120]
    started = time.monotonic()
    try:
        with urllib.request.urlopen("https://pypi.org/simple/six/", timeout=20) as answer:
            report["via_proxy"] = answer.status
    except OSError as exc:
        report["via_proxy"] = f"failed: {exc}"[:120]
    report["via_proxy_seconds"] = round(time.monotonic() - started, 2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--loopback-key", required=True)
    parser.add_argument("digests", nargs="*")
    args = parser.parse_args()
    digests = dict(item.split("=", 1) for item in args.digests)
    seen, in_processes = processes(digests, args.loopback_key)
    report = {
        "uid": os.getuid(),
        "gids": os.getgroups(),
        "processes": seen,
        "secrets_in_processes": sorted(in_processes),
        "secrets_in_files": sorted(files(["/workspace", "/exchange", "/tmp"], digests)),
        "egress": egress(),
    }
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"probe: {len(seen)} processes, secrets found: {sorted(in_processes) or 'none'}")


if __name__ == "__main__":
    main()
