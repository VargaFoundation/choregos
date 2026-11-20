# SPDX-License-Identifier: Apache-2.0
"""The relay: a job step's way to a connector operation, through the platform's API (ADR 0198 point 7).

`POST {TRANIBOR_API_URL}/runs/{TRANIBOR_RUN_ID}/tools/{connector}__{operation}` with the run token. The
name is the one the API gives a brokered tool (`services.broker.SEPARATOR`): the connector's name, two
underscores, the operation's. The operations a step may call are the `tools: [{connector, tools}]` of
its actor in the workflow, copied into the run when it starts: any other name is refused (404).

Standard library only. A relay that fails raises `RelayError`, a `job.ContractError`: the command exits
non-zero and writes nothing, so a fact never reads 0 for what was not asked.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Final

from tranibor_procurement import job

#: The connector of the plugin's server (ADR 0198 point 7).
CONNECTOR: Final = "purchasing"
#: The separator of a tool's name: the connector, then the operation (`services.broker.SEPARATOR`).
SEPARATOR: Final = "__"
DEFAULT_TIMEOUT_S: Final = 60.0
#: The size of a page asked of an operation, and the most pages one call collects.
PAGE_SIZE: Final = 100
MAX_PAGES: Final = 50

#: What a step calls: `(operation, arguments)` of the connector `purchasing`, the operation's answer.
Relay = Callable[[str, dict[str, Any]], dict[str, Any]]


class RelayError(job.ContractError):
    """The platform did not give an answer: unreachable, refused, or not a result."""


def tool_name(operation: str, connector: str = CONNECTOR) -> str:
    return f"{connector}{SEPARATOR}{operation}"


def parse_response(payload: Any, tool: str) -> dict[str, Any]:
    """The API answers `{status_code, result}`, where `result` is the operation's structured answer (or
    an MCP result holding it as `structuredContent`). A status other than 200 is a refusal."""
    if not isinstance(payload, dict):
        raise RelayError(f"{tool}: the response is not a JSON object")
    status = payload.get("status_code", 200)
    if status != 200:
        raise RelayError(f"{tool}: the connector answered {status}")
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        result = result["structuredContent"]
    if not isinstance(result, dict):
        raise RelayError(f"{tool}: the response holds no result object")
    return result


class HttpRelay:
    """A `Relay` over HTTP, configured by the run's environment."""

    def __init__(self, env: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        e = os.environ if env is None else env
        try:
            self._base = f"{e['TRANIBOR_API_URL'].rstrip('/')}/runs/{e['TRANIBOR_RUN_ID']}/tools/"
            self._token = e["TRANIBOR_RUN_TOKEN"]
        except KeyError as exc:
            raise RelayError(f"missing environment variable {exc.args[0]}") from exc
        self._timeout = timeout

    def __call__(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = tool_name(operation)
        request = urllib.request.Request(  # noqa: S310 - the URL comes from the run's own environment
            self._base + tool,
            data=json.dumps(arguments).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return parse_response(json.loads(response.read()), tool)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise RelayError(f"{tool}: {exc}") from exc


def collect(relay: Relay, operation: str, arguments: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Every row of an operation's pages: the list under `key` (else `items`), following `next_cursor`.

    An answer with no list is an error, not an empty result: `[]` must be what the server said."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page_arguments = {**arguments, "limit": PAGE_SIZE, **({"cursor": cursor} if cursor else {})}
        answer = relay(operation, page_arguments)
        page = answer.get(key, answer.get("items"))
        if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
            raise RelayError(f"{tool_name(operation)}: the answer holds no list `{key}`")
        rows.extend(page)
        next_cursor = answer.get("next_cursor")
        if not next_cursor:
            return rows
        cursor = str(next_cursor)
    raise RelayError(f"{tool_name(operation)}: more than {MAX_PAGES} pages, narrow the request")
