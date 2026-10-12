#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the local install (`compose.yaml`) the way a person would use it, and measure it.

    docker compose up -d
    python3 tools/local_install_check.py               # console, sign-in, one work item
    python3 tools/local_install_check.py --console-only
    python3 tools/local_install_check.py --expect-real --stage-timeout 900   # a key in .env

What it does, against the running compose:

1. waits until the console answers 200 on `/login` (and says how long it took);
2. signs in with development sign-in, through the console's `/api/v1` relay;
3. creates a work item in the demo project and waits until the workflow has run its agent stage
   and asks a person to approve (`awaiting_approval`);
4. approves it, and waits for the terminal state (`approved`);
5. prints the memory of every container of the compose project.

Standard library only: it runs on any machine with Python 3, with nothing installed. Exit code 0
when everything held, 1 otherwise, with the step that failed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class Console:
    """An HTTP client with a cookie jar: the session cookie the API sets is sent back."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - an http(s) URL given by --url
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            return status, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return status, raw.decode(errors="replace")

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self.request("POST", path, body)


def step(name: str) -> None:
    print(f"· {name}", flush=True)


def fail(message: str) -> int:
    print(f"✗ {message}", file=sys.stderr, flush=True)
    return 1


def wait_for_console(console: Console, timeout_s: float) -> float | None:
    """Seconds until `/login` answers 200, or None at the deadline."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            status, _ = console.get("/login")
            if status == 200:
                return time.monotonic() - start
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(2)
    return None


def wait_for_state(console: Console, item_id: str, states: set[str], timeout_s: float) -> dict[str, Any]:
    """The work item once its state is one of `states`, or as last read at the deadline."""
    deadline = time.monotonic() + timeout_s
    item: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = console.get(f"/api/v1/work-items/{item_id}")
        if status == 200 and isinstance(body, dict):
            item = body
            if item.get("state") in states:
                return item
        time.sleep(2)
    return item


def memory_by_container(project: str) -> list[tuple[str, float]]:
    """`docker stats` of the compose project's running containers, in MiB."""
    ids = subprocess.run(  # noqa: S603 - fixed argv, `docker` from PATH
        ["docker", "compose", "-p", project, "ps", "-q"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    if not ids:
        return []
    out = subprocess.run(  # noqa: S603 - fixed argv, `docker` from PATH
        ["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.MemUsage}}", *ids],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "B": 1 / 1024 / 1024}
    rows: list[tuple[str, float]] = []
    for line in out.splitlines():
        name, _, usage = line.partition(";")
        used = usage.split(" / ")[0].strip()
        for unit, factor in units.items():
            if used.endswith(unit):
                rows.append((name, float(used.removesuffix(unit)) * factor))
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://localhost:3000", help="the console (default: %(default)s)")
    parser.add_argument("--email", default="admin@choregos.local", help="development sign-in e-mail")
    parser.add_argument("--project", default="demo:demo", help="org:project of the demo project")
    parser.add_argument("--compose-project", default="choregos", help="compose project name, for memory")
    parser.add_argument("--console-timeout", type=float, default=300, help="seconds to wait for the console")
    parser.add_argument("--stage-timeout", type=float, default=180, help="seconds to wait for each state")
    parser.add_argument("--console-only", action="store_true", help="stop after the console answers")
    parser.add_argument(
        "--expect-real",
        action="store_true",
        help="fail unless the agent stage consumed model tokens (a key in .env, S28-02)",
    )
    args = parser.parse_args()

    console = Console(args.url)
    step(f"waiting for the console on {args.url}/login")
    waited = wait_for_console(console, args.console_timeout)
    if waited is None:
        return fail(f"the console did not answer 200 within {args.console_timeout:.0f} s")
    print(f"  console 200 after {waited:.0f} s of waiting")

    if not args.console_only:
        step(f"development sign-in as {args.email}")
        status, _ = console.get(f"/api/v1/auth/login?as={args.email}&redirect_to=/")
        status, me = console.get("/api/v1/me")
        if status != 200:
            return fail(f"/api/v1/me answered {status} after sign-in: {me}")

        step(f"creating a work item in {args.project}")
        status, item = console.post(
            f"/api/v1/projects/{args.project}/work-items",
            {"title": "Plan the team offsite", "body": "Two days, twelve people, near Lyon, in May."},
        )
        if status != 201 or not isinstance(item, dict):
            return fail(f"creating the work item answered {status}: {item}")
        print(f"  {item['tracker_key']} created in state `{item['state']}`")

        step("waiting for the agent stage and the request for approval")
        started = time.monotonic()
        item = wait_for_state(console, item["id"], {"awaiting_approval", "needs_human"}, args.stage_timeout)
        if item.get("state") != "awaiting_approval":
            return fail(f"the work item is `{item.get('state')}`, expected `awaiting_approval`: {item}")
        print(f"  `awaiting_approval` after {time.monotonic() - started:.0f} s")
        totals = item.get("totals") or {}
        print(f"  tokens in/out: {totals.get('tokens_in', 0)}/{totals.get('tokens_out', 0)}")
        if args.expect_real and not totals.get("tokens_in"):
            return fail("--expect-real: the agent stage consumed no model token, it was simulated")

        step("approving")
        status, body = console.post(f"/api/v1/work-items/{item['id']}/decisions", {"kind": "approve"})
        if status != 202:
            return fail(f"the decision answered {status}: {body}")
        item = wait_for_state(console, item["id"], {"approved"}, args.stage_timeout)
        if item.get("state") != "approved":
            return fail(f"the work item is `{item.get('state')}` after approval, expected `approved`")
        print("  `approved`: the workflow reached its terminal state")

    rows = memory_by_container(args.compose_project)
    if rows:
        step("memory")
        for name, mib in sorted(rows):
            print(f"  {name:<32} {mib:8.0f} MiB")
        print(f"  {'total':<32} {sum(m for _, m in rows):8.0f} MiB")
    print("✓ the local install works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
