# SPDX-License-Identifier: Apache-2.0
"""Where the proxy learns a run's network: the API, or a scriptable fake (ADR 0005)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from ..schemas.network import RunNetwork


class UnknownRunError(Exception):
    """The API does not know these credentials (a wrong token, another run, a run that ended)."""


class SourceUnavailableError(Exception):
    """The API could not be asked: the proxy fails closed."""


class RunNetworkSource(Protocol):
    async def get(self, run_id: str, token: str) -> RunNetwork: ...


class HttpRunNetworkSource:
    """The API's `GET /internal/runs/{id}/network`, authenticated with the run's own token.

    The answer is kept `cache_seconds`, keyed by the run AND a hash of its token, so that a second
    token for the same run never reads the first one's answer. A refusal is remembered for a few
    seconds only, so that a pod hammering the proxy with a wrong token does not hammer the API.
    """

    def __init__(
        self,
        base_url: str,
        *,
        cache_seconds: float = 60,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        refusal_seconds: float = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_seconds = cache_seconds
        self.refusal_seconds = refusal_seconds
        self.client = client or httpx.AsyncClient(timeout=10)
        self.clock = clock
        self._cache: dict[tuple[str, str], tuple[float, RunNetwork | None]] = {}

    async def get(self, run_id: str, token: str) -> RunNetwork:
        key = (run_id, hashlib.sha256(token.encode()).hexdigest())
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and hit[0] > now:
            if hit[1] is None:
                raise UnknownRunError(run_id)
            return hit[1]
        try:
            response = await self.client.get(
                f"{self.base_url}/internal/runs/{run_id}/network",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            raise SourceUnavailableError(str(error)) from error
        if response.status_code in (401, 403, 404):
            self._cache[key] = (now + self.refusal_seconds, None)
            raise UnknownRunError(run_id)
        if response.status_code != 200:
            raise SourceUnavailableError(f"the API answered {response.status_code}")
        network = RunNetwork.model_validate(response.json())
        self._cache[key] = (now + self.cache_seconds, network)
        if len(self._cache) > 4096:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return network

    async def aclose(self) -> None:
        await self.client.aclose()


class FakeRunNetworkSource:
    """Scriptable: `networks[(run_id, token)]`; a pair it does not know is an `UnknownRunError`."""

    def __init__(self, networks: dict[tuple[str, str], RunNetwork] | None = None) -> None:
        self.networks = networks or {}
        self.calls = 0
        self.unavailable = False

    def put(self, token: str, **fields: Any) -> RunNetwork:
        network = RunNetwork(**{"org": "varga", "project": "billing", **fields})
        self.networks[(network.run_id, token)] = network
        return network

    async def get(self, run_id: str, token: str) -> RunNetwork:
        self.calls += 1
        if self.unavailable:
            raise SourceUnavailableError("scripted")
        try:
            return self.networks[(run_id, token)]
        except KeyError:
            raise UnknownRunError(run_id) from None
