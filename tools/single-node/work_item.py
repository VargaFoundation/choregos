# SPDX-License-Identifier: Apache-2.0
"""The proof's client, run INSIDE the API pod (`kubectl exec -i … -- python - <command>`).

    run <org> <token>      seed the demo project, take its work item to `approved`, print its id
    check <org> <token> <id>   print the work item's state; exit 1 unless it is `approved`

The demo project (`tranibor_api.local_demo`) has a simulated agent: every declared output filled, no
model called. Everything else is the platform's own path: the API, the database, Temporal, the
workers, the gates and a person's decision.

A decision is the person's own gesture (ADR 0154 §7): the API refuses it from an API token
(`decision_requires_session`). The proof has no identity provider to sign in through, so the
approval is sent with a console session opened the way the sign-in opens one: a recorded
`user_sessions` row (channel `proof`) and the cookie the API signs for it with the site's session
secret (`person_session`, the API's own modules, the only part that is not standard library).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = "http://localhost:8000/api/v1"


def call(
    method: str, path: str, token: str, body: dict[str, Any] | None = None, *, cookie: str | None = None
) -> tuple[int, Any]:
    """`token` as a bearer, or, with `cookie`, the console's session cookie and no bearer."""
    headers = {"Content-Type": "application/json"}
    if cookie is None:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Cookie"] = cookie
    request = urllib.request.Request(  # noqa: S310 - a fixed local URL
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw.decode(errors="replace")


def person_session(user_id: str) -> str:
    """A console session of `user_id` for one hour, as `name=value` of the cookie the API reads."""
    import asyncio
    from datetime import timedelta

    from tranibor_api.browser_sessions import open_session
    from tranibor_api.config import get_settings
    from tranibor_api.db.models import User
    from tranibor_api.db.session import session_scope
    from tranibor_api.security import sign_session
    from tranibor_core import utcnow

    async def opened() -> str:
        settings = get_settings()
        now = utcnow()
        async with session_scope(orgs="*") as session:
            user = await session.get(User, user_id)
            if user is None:
                raise SystemExit(f"no user {user_id}")
            row = await open_session(session, user, channel="proof", expires_at=now + timedelta(hours=1))
            payload = {
                "sub": user.id,
                "sid": row.id,
                "iat": int(now.timestamp()),
                "exp": int(row.expires_at.timestamp()),
            }
        return f"{settings.session_cookie}={sign_session(payload, settings)}"

    return asyncio.run(opened())


def wait_for(token: str, item_id: str, states: set[str], timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    item: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = call("GET", f"/work-items/{item_id}", token)
        if status == 200 and isinstance(body, dict):
            item = body
            if item.get("state") in states:
                return item
        time.sleep(3)
    raise SystemExit(
        f"work item {item_id} is `{item.get('state')}` after {timeout_s:.0f} s, expected {states}"
    )


def run(org: str, token: str) -> None:
    # Its log goes to stderr: stdout carries the work item id only, which the proof reads.
    subprocess.run([sys.executable, "-m", "tranibor_api.local_demo"], check=True, stdout=sys.stderr)
    status, page = call("GET", f"/projects/{org}:demo/work-items", token)
    if status != 200 or not isinstance(page, dict) or not page.get("items"):
        raise SystemExit(f"the demo project's work items answered {status}: {page}")
    item = page["items"][0]
    print(f"work item {item['tracker_key']} ({item['id']}) is `{item['state']}`", file=sys.stderr)
    item = wait_for(token, item["id"], {"awaiting_approval", "needs_human"}, 600)
    if item["state"] != "awaiting_approval":
        raise SystemExit(f"the work item went to `{item['state']}`: {item}")
    status, me = call("GET", "/me", token)
    if status != 200 or not isinstance(me, dict):
        raise SystemExit(f"/me answered {status}: {me}")
    cookie = person_session(me["id"])
    status, body = call(
        "POST", f"/work-items/{item['id']}/decisions", token, {"kind": "approve"}, cookie=cookie
    )
    if status != 202:
        raise SystemExit(f"the approval answered {status}: {body}")
    item = wait_for(token, item["id"], {"approved"}, 300)
    print(f"work item {item['tracker_key']} is `approved`", file=sys.stderr)
    print(item["id"])


def check(token: str, item_id: str) -> None:
    status, item = call("GET", f"/work-items/{item_id}", token)
    state = item.get("state") if isinstance(item, dict) else None
    print(f"work item {item_id}: HTTP {status}, state `{state}`")
    if status != 200 or state != "approved":
        raise SystemExit(1)


def main() -> None:
    command, *rest = sys.argv[1:]
    if command == "run":
        run(rest[0], rest[1])
    elif command == "check":
        check(rest[1], rest[2])
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()
