# SPDX-License-Identifier: Apache-2.0
"""The model, as the run gives it: through the platform's gateway, with the run's own key.

The backend puts `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL` (or the `ANTHROPIC_*` ones
for an `anthropic` runtime) in the environment. The key is the run's: it carries the run's budget,
every call is a line in the ledger, and no provider key is ever in the pod. `ctx.llm` reads them;
there is nothing to configure.

Any framework's model client can be counted: give it `ctx.llm.http_client()` (or
`ctx.llm.openai_client()`). Every model request that goes through it is one turn of the run, is
reported to the runner like a tool call, and is refused before it leaves once the step's
`max_turns` is reached.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from .errors import LlmError, sdk_error_in
from .session import RunSession

#: The paths of a model request, whichever API format the gateway speaks.
MODEL_PATHS = ("/chat/completions", "/responses", "/messages", "/completions")


def _httpx() -> ModuleType:
    """The HTTP library the installed `openai` is built on (`httpx2` from openai 3, `httpx` before):
    a client of another family is refused by `AsyncOpenAI`."""
    try:
        import openai._base_client as base

        for name in ("httpx2", "httpx"):
            module = getattr(base, name, None)
            if module is not None:
                return module  # type: ignore[no-any-return]
    except ImportError:
        pass
    try:
        import httpx2

        return httpx2
    except ImportError:
        import httpx

        return httpx


def _counting_transport(httpx_module: ModuleType, session: RunSession, default_model: str | None) -> Any:
    """A transport that makes each model request one guarded call of the session."""
    inner = httpx_module.AsyncHTTPTransport()

    class CountingTransport(httpx_module.AsyncBaseTransport):  # type: ignore[name-defined, misc]
        async def handle_async_request(self, request: Any) -> Any:
            path = request.url.path.rstrip("/")
            if request.method != "POST" or not path.endswith(MODEL_PATHS):
                return await inner.handle_async_request(request)
            try:
                body = request.content
            except Exception:  # a streamed request body: its size and digest are not known
                body = b""
            try:
                model = str(json.loads(body).get("model") or default_model)
            except (ValueError, AttributeError):
                model = str(default_model)
            shown = {"model": model, "bytes": len(body), "digest": hashlib.sha256(body).hexdigest()[:12]}

            async def send() -> Any:
                return await inner.handle_async_request(request)

            return await session.guarded(f"llm {model}", "other", shown, send, ask=False)

        async def aclose(self) -> None:
            await inner.aclose()

    return CountingTransport()


@dataclass(frozen=True)
class LlmReply:
    text: str
    raw: Any = None


class Llm:
    """The run's model. Every call made through `http_client()` is counted and journaled."""

    def __init__(self, session: RunSession, env: Mapping[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self._session = session
        if env.get("ANTHROPIC_BASE_URL"):
            self.api_format = "anthropic"
            self.base_url: str | None = env.get("ANTHROPIC_BASE_URL")
            self.api_key: str | None = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
            self.model: str | None = env.get("ANTHROPIC_MODEL")
        else:
            self.api_format = "openai"
            self.base_url = env.get("OPENAI_BASE_URL")
            self.api_key = env.get("OPENAI_API_KEY")
            self.model = env.get("OPENAI_MODEL")
        self._http: Any = None
        self._client: Any = None

    @property
    def available(self) -> bool:
        """The run has a gateway to call (a base URL and a key)."""
        return bool(self.base_url and self.api_key and self.model)

    def http_client(self) -> Any:
        """An async HTTP client whose model requests count against the step's `max_turns`.

        Hand it to the framework's model client: LangChain `ChatOpenAI(http_async_client=...)`,
        `anthropic.AsyncAnthropic(http_client=...)`, an `openai.AsyncOpenAI(http_client=...)`.
        Requests to other paths pass through uncounted; a streamed answer passes through intact.
        """
        if self._http is None:
            module = _httpx()
            self._http = module.AsyncClient(
                transport=_counting_transport(module, self._session, self.model),
                timeout=module.Timeout(600.0),
            )
        return self._http

    def openai_client(self) -> Any:
        """An `openai.AsyncOpenAI` on the gateway, with the run's key, counted (see `http_client`)."""
        if self.api_format != "openai":
            raise LlmError(
                "this agent's runtime is `anthropic`: build the Anthropic client on `base_url`/`api_key`"
            )
        if not self.available:
            raise LlmError("this run has no gateway: no OPENAI_BASE_URL / OPENAI_API_KEY in its environment")
        if self._client is None:
            from openai import AsyncOpenAI

            # No retry: a refused call (the turn limit) must not be tried again behind the agent's back.
            self._client = AsyncOpenAI(
                base_url=self.base_url, api_key=self.api_key, http_client=self.http_client(), max_retries=0
            )
        return self._client

    @property
    def client(self) -> Any:
        """The same as `openai_client()`: kept for the code written against it."""
        return self.openai_client()

    async def chat(self, messages: list[dict[str, Any]], **params: Any) -> LlmReply:
        """One chat completion; a turn like any other model call."""
        client = self.openai_client()  # refuse early, before a turn is spent on a call that cannot happen
        try:
            completion = await client.chat.completions.create(
                model=params.pop("model", self.model), messages=messages, **params
            )
        except Exception as exc:
            if (sdk := sdk_error_in(exc)) is not None:
                raise sdk from exc
            raise LlmError(f"the model call failed: {type(exc).__name__}: {exc}"[:500]) from exc
        return LlmReply(text=str(completion.choices[0].message.content or ""), raw=completion)
