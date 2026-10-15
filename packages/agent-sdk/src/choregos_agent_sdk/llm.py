# SPDX-License-Identifier: Apache-2.0
"""The model, as the run gives it: through the platform's gateway, with the run's own key.

The backend puts `OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL` (or the `ANTHROPIC_*` ones
for an `anthropic` runtime) in the environment. The key is the run's: it carries the run's budget,
every call is a line in the ledger, and no provider key is ever in the pod. `ctx.llm` reads them;
there is nothing to configure.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import LlmError
from .session import RunSession


@dataclass(frozen=True)
class LlmReply:
    text: str
    raw: Any = None


class Llm:
    """The run's model. `chat` counts as one turn and is reported to the runner like a tool call."""

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
        self._client: Any = None

    @property
    def available(self) -> bool:
        """The run has a gateway to call (a base URL and a key)."""
        return bool(self.base_url and self.api_key and self.model)

    @property
    def client(self) -> Any:
        """The `openai.AsyncOpenAI` client on the gateway, for what `chat` does not cover.

        A call made on it directly is not counted as a turn; the run's key still bounds its spend.
        """
        if self.api_format != "openai":
            raise LlmError(
                "this agent's runtime is `anthropic`: build the Anthropic client on `base_url`/`api_key`"
            )
        if not self.available:
            raise LlmError("this run has no gateway: no OPENAI_BASE_URL / OPENAI_API_KEY in its environment")
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    async def chat(self, messages: list[dict[str, Any]], **params: Any) -> LlmReply:
        """One chat completion. Counts as a turn (`max_turns`) and is journaled by the runner
        as a call to the model — its arguments are sizes and a digest, never the prompt."""
        client = self.client  # refuse early, before a turn is spent on a call that cannot happen
        digest = hashlib.sha256(repr(messages).encode()).hexdigest()[:12]
        shown = {"model": self.model, "messages": len(messages), "digest": digest}

        async def call() -> LlmReply:
            try:
                completion = await client.chat.completions.create(
                    model=params.pop("model", self.model), messages=messages, **params
                )
            except Exception as exc:
                raise LlmError(f"the model call failed: {type(exc).__name__}: {exc}"[:500]) from exc
            choice = completion.choices[0].message.content or ""
            return LlmReply(text=str(choice), raw=completion)

        return await self._session.guarded(f"llm {self.model}", "other", shown, call, ask=False)
