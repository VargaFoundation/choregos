# SPDX-License-Identifier: Apache-2.0
"""`choregos login` as the person: the device flow of RFC 8628, brokered by the console (ADR 0154 §6).

The CLI asks the API for two codes, shows the short one and a link, and polls while the person approves it in
the console, signed in as themselves. What comes back is a session of the person's own (not an API token):
the CLI stores it in the profile like a token and never prints it, nor takes it as an argument.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import Any

import httpx

from . import __version__

GRANT = "urn:ietf:params:oauth:grant-type:device_code"
#: Replaced by the tests: a poll must not wait five real seconds.
sleep: Callable[[float], None] = time.sleep
clock: Callable[[], float] = time.monotonic


class DeviceSignInError(RuntimeError):
    """The sign-in did not complete: refused, expired, or the API said something unexpected."""


def client_name() -> str:
    """What the console shows the person before they approve: this tool, its version and where it runs."""
    return f"tranibor {__version__} on {socket.gethostname()}"[:80]


def start(http: httpx.Client, *, org: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"client_kind": "cli", "client_name": client_name()}
    if org:
        body["org"] = org
    response = http.post("/auth/device", json=body)
    if response.status_code != 200:
        raise DeviceSignInError(
            f"the API refused to start a sign-in ({response.status_code}): {response.text[:200]}"
        )
    codes: dict[str, Any] = response.json()
    return codes


def link(codes: dict[str, Any], *, fresh: bool) -> str:
    """The link to open: the code is in it, so the person does not retype it; `fresh` asks the console to make
    them sign in through their identity provider again first."""
    url = str(codes["verification_uri_complete"])
    return f"{url}&fresh=1" if fresh else url


def wait_for_session(http: httpx.Client, codes: dict[str, Any]) -> dict[str, Any]:
    """Poll until the person decides. Honours `interval` and `slow_down`; gives up when the code expires."""
    interval = float(codes["interval"])
    deadline = clock() + float(codes["expires_in"])
    while clock() < deadline:
        sleep(interval)
        response = http.post(
            "/auth/device/token", json={"grant_type": GRANT, "device_code": codes["device_code"]}
        )
        if response.status_code == 200:
            token: dict[str, Any] = response.json()
            return token
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        error = problem.get("error") if isinstance(problem, dict) else None
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = float(problem.get("interval", interval + 5))
            continue
        if error == "access_denied":
            raise DeviceSignInError("the sign-in was refused in the console")
        if error == "expired_token":
            raise DeviceSignInError("the code expired before it was approved: run `choregos login` again")
        raise DeviceSignInError(
            f"unexpected answer while waiting ({response.status_code}): {response.text[:200]}"
        )
    raise DeviceSignInError("the code expired before it was approved: run `choregos login` again")
