# SPDX-License-Identifier: Apache-2.0
"""Helpers for the tests of the public pages (`docs/site/`, S62-09b): run a page's CLI command for real.

`run_cli` runs a `choregos …` command line, as a page prints it, through the CLI's own Typer app, and
sends the HTTP requests the command makes to the test's ASGI application instead of a server. The CLI
is synchronous, so the command runs in a thread while the event loop serves its requests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest import mock

from httpx import AsyncClient
from typer.testing import CliRunner, Result

SITE = Path(__file__).resolve().parents[3] / "docs" / "site"
#: The pages' organisation, and the test fixtures' one.
PAGE_ORG, TEST_ORG = "acme", "varga"


class _Forward:
    """The CLI's `Client`, whose requests go to the test application."""

    def __init__(self, http: AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self._http, self._loop = http, loop

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        from choregos_cli.client import ApiError

        call = self._http.request(method, "/api/v1" + path, **kwargs)
        response = asyncio.run_coroutine_threadsafe(call, self._loop).result(30)
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.json() if response.content else "")
        return response.json() if response.content else None

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)


def to_test_org(words: list[str]) -> list[str]:
    return [w.replace(f"{PAGE_ORG}:", f"{TEST_ORG}:") for w in words]


async def run_cli(http: AsyncClient, words: list[str], cwd: Path | None = None) -> Result:
    """Run `choregos <words[1:]>` against the test application, with the session `http` holds."""
    from choregos_cli import main

    assert words[0] == "choregos", words
    loop = asyncio.get_running_loop()
    forward = _Forward(http, loop)

    def invoke() -> Result:
        with mock.patch.object(main, "client", lambda: forward):
            runner = CliRunner()
            if cwd is None:
                return runner.invoke(main.app, to_test_org(words[1:]))
            import os

            before = os.getcwd()
            os.chdir(cwd)
            try:
                return runner.invoke(main.app, to_test_org(words[1:]))
            finally:
                os.chdir(before)

    return await asyncio.to_thread(invoke)
