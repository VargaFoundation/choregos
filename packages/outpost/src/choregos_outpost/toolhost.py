# SPDX-License-Identifier: Apache-2.0
"""The tool host: a call made against an alias's endpoint, from inside the outpost's network.

It uses the MCP client of `choregos_adapters` as it is (`McpClient`: `initialize`, `tools/list`,
`tools/call` over Streamable HTTP). Around it, four things are the outpost's own:

- **the credential** is read from the file the policy names, at each call, so that a rotated one
  is picked up without a restart. It never leaves the outpost (ADR 0147 §7): it is remembered for
  masking as soon as it is read, and no log line and no answer carries it;
- **one host**: the client's transport is the guarded one, and the egress policy put in context
  for a call opens the endpoint's own host and port and nothing else. A redirect elsewhere is
  refused before it is sent. The daemon calls the door and its endpoints, and no third place
  (ADR 0147 §9);
- **the timeout**: a call is abandoned after the endpoint's `timeout_s`, or at the call's
  deadline if that comes first;
- **the payload ceiling**: an answer is read up to a bound, never into memory without one.

What a failure means depends on whether the tool call had left when it happened, which the
transport is watched for. Before `tools/call` was sent, the server received no call:
`EndpointUnavailableError`, a refusal. Once it was sent, the daemon reports what it knows: `error` when
the server answered a JSON-RPC error (it says it did not do the call), `oversized` when its answer
is above the ceiling, and `unanswered` for everything else (a timeout, a lost connection, an HTTP
error): executed or not, nobody knows.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from choregos_adapters.mcp import McpClient, McpError, McpTool
from choregos_contracts.enums import OutpostAnswerOutcome
from choregos_contracts.outpost import OutpostEndpoint, OutpostPolicy, OutpostServerError
from choregos_core.egress import AllowedRule, EgressPolicy, host_port_of, use_policy
from choregos_core.redaction import redact_known, remember_secret

from . import __version__
from .policy import ceiling, weight

#: A transport for one call; the MCP client closes it with the call.
TransportFactory = Callable[[], httpx.AsyncBaseTransport]
#: What reading an endpoint can raise besides the client's own error: the network, a timeout, and a
#: listing whose entries are not what the protocol describes.
_FAILURES = (httpx.HTTPError, TimeoutError, OSError, LookupError, TypeError, ValueError, AttributeError)
_CALL = "tools/call"


class EndpointUnavailableError(Exception):
    """The outpost could not reach or open its own server, and sent it no call."""


class ResultTooLargeError(Exception):
    """An answer of the endpoint's server is above what the outpost reads."""


@dataclass(frozen=True)
class Made:
    """What the daemon knows of a call it took to its server."""

    outcome: OutpostAnswerOutcome
    duration_ms: int
    result: dict[str, Any] | None = None
    error: OutpostServerError | None = None


def _method_of(request: httpx.Request) -> str:
    try:
        body = json.loads(request.content)
    except (ValueError, httpx.RequestNotRead):
        return ""
    return str(body.get("method", "")) if isinstance(body, dict) else ""


class _Watched(httpx.AsyncBaseTransport):
    """The transport of one call: it notes which JSON-RPC methods left and what came back, and reads
    no answer beyond `limit` bytes, decoded."""

    def __init__(self, inner: httpx.AsyncBaseTransport, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self.sent: list[str] = []
        self.status: dict[str, int] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        method = _method_of(request)
        self.sent.append(method)
        response = await self._inner.handle_async_request(request)
        body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._limit:
                    raise ResultTooLargeError(method)
        finally:
            await response.aclose()
        self.status[method] = response.status_code
        # The body is decoded now: the headers that described its encoding no longer apply.
        dropped = {"content-encoding", "content-length", "transfer-encoding"}
        headers = [
            (name, value) for name, value in response.headers.multi_items() if name.lower() not in dropped
        ]
        return httpx.Response(response.status_code, headers=headers, content=bytes(body))

    async def aclose(self) -> None:
        await self._inner.aclose()


def _only(endpoint: OutpostEndpoint) -> EgressPolicy:
    """The egress policy of one call: the endpoint's own host and port, enforced."""
    target = host_port_of(endpoint.url)
    if target is None:
        raise EndpointUnavailableError("the endpoint's URL names no host")
    host, port = target
    return EgressPolicy(rules=(AllowedRule(host=host, ports=(port,), used_by=("api",)),), mode="enforce")


class ToolHost:
    def __init__(
        self,
        policy: OutpostPolicy,
        *,
        transport: TransportFactory | None = None,
        verify: ssl.SSLContext | None = None,
    ) -> None:
        self._policy = policy
        self._verify = verify
        self._transport = transport or self._direct

    def _direct(self) -> httpx.AsyncBaseTransport:
        """Straight to the endpoint: the outbound proxy is the door's path, not the local network's."""
        if self._verify is None:
            return httpx.AsyncHTTPTransport(trust_env=False)
        return httpx.AsyncHTTPTransport(verify=self._verify, trust_env=False)

    async def _credential(self, endpoint: OutpostEndpoint) -> str:
        if endpoint.auth is None:
            return ""
        try:
            text = await asyncio.to_thread(Path(endpoint.auth.from_file).read_text, "utf-8")
        except (OSError, ValueError) as error:
            reason = error.strerror if isinstance(error, OSError) else "not text"
            raise EndpointUnavailableError(f"the credential file cannot be read ({reason})") from error
        credential = text.strip()
        if not credential:
            raise EndpointUnavailableError("the credential file is empty")
        remember_secret(credential)
        return credential

    async def _client(self, alias: str) -> tuple[McpClient, _Watched, OutpostEndpoint]:
        endpoint = self._policy.endpoints[alias]
        credential = await self._credential(endpoint)
        watched = _Watched(self._transport(), 8 * ceiling(endpoint) + 65536)
        client = McpClient(
            endpoint.url,
            token=credential,
            auth_header=endpoint.auth.header if endpoint.auth else "Authorization",
            timeout_s=endpoint.timeout_s,
            transport=watched,
            user_agent=f"choregos-outpost/{__version__}",
        )
        return client, watched, endpoint

    async def listing(self, alias: str, *, within_s: float) -> list[McpTool]:
        """The tools the alias's own server lists, each with what it declares of itself
        (`readOnlyHint`). `EndpointUnavailableError` when the server cannot be reached or opened."""
        client, _, endpoint = await self._client(alias)
        try:
            with use_policy(_only(endpoint), origin=f"outpost:{alias}"):
                async with asyncio.timeout(within_s):
                    return await client.list_tools()
        except McpError as error:
            raise EndpointUnavailableError(redact_known(str(error))) from error
        except _FAILURES as error:
            raise EndpointUnavailableError(redact_known(str(error)) or type(error).__name__) from error

    async def call(self, alias: str, tool: str, arguments: dict[str, Any], *, within_s: float) -> Made:
        """Makes one tool call. `EndpointUnavailableError` when no `tools/call` left the outpost; a `Made`
        otherwise, whose outcome says what the daemon knows."""
        client, watched, endpoint = await self._client(alias)
        started = time.monotonic()

        def made(outcome: OutpostAnswerOutcome, **carried: Any) -> Made:
            return Made(outcome, int((time.monotonic() - started) * 1000), **carried)

        try:
            with use_policy(_only(endpoint), origin=f"outpost:{alias}"):
                async with asyncio.timeout(within_s):
                    result = await client.call_tool(tool, arguments)
        except ResultTooLargeError as error:
            if _CALL not in watched.sent:
                raise EndpointUnavailableError(
                    "the server's handshake is above what the outpost reads"
                ) from error
            return made(OutpostAnswerOutcome.OVERSIZED)
        except McpError as error:
            if _CALL not in watched.sent:
                raise EndpointUnavailableError(redact_known(str(error))) from error
            # A JSON-RPC error: an answer the server gave, with the integer code the protocol requires.
            # Anything else (an HTTP error, an answer nobody can read) does not say the call was not made.
            if watched.status.get(_CALL, 500) < 400 and isinstance(error.code, int):
                message = redact_known(str(error))[:2000]
                return made(
                    OutpostAnswerOutcome.ERROR, error=OutpostServerError(message=message, code=error.code)
                )
            return made(OutpostAnswerOutcome.UNANSWERED)
        except _FAILURES as error:
            if _CALL not in watched.sent:
                raise EndpointUnavailableError(redact_known(str(error)) or type(error).__name__) from error
            return made(OutpostAnswerOutcome.UNANSWERED)
        if weight(result) > ceiling(endpoint):
            return made(OutpostAnswerOutcome.OVERSIZED)
        return made(OutpostAnswerOutcome.RESULT, result=result)
