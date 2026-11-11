# SPDX-License-Identifier: Apache-2.0
"""The default `SuitePlayer`: a conversation suite played on the platform's model gateway (S63-17, ADR 0140).

An installed assistant's suites must not be a hole in production: without a registered player the gate used
to add no check. This player is what runs when none is registered. It plays each case of a suite through
the real conversation machinery (the prompt loop, `choregos_core.conversation_evals.play_case`) on the
version's own instructions and model, the tools answering from the sources' fakes the suite declares
(`tool_results`), then scores the rubric of every case that passed its assertions with the judge model
(`choregos_core.conversation_judge`). Both go through the gateway, the same path the platform's other
side calls take (`/chat/completions` with the deployment's key): no provider SDK.

When it cannot play (no gateway key, or an organisation that brings its own gateway, on which the
deployment's key is no good) it raises `CannotPlayError` and the promotion is refused with the
reason, unless the
deployment opted out with `conversation_suites_require_model = false`.
"""

from __future__ import annotations

from typing import Any

import httpx
from choregos_core.conversation_evals import SuiteResult, loop_tools, play_suite, tools_named
from choregos_core.conversation_judge import judge_with
from choregos_core.prompt_loop import Complete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import Agent, AgentVersion, AssistantInstallation
from ..schemas import AgentSpec
from .gateway import org_brings_its_gateway

TIMEOUT_SECONDS = 120.0


class CannotPlayError(Exception):
    """The suite cannot be played on this deployment; the message is the reason the promotion shows."""


class ModelRefusedError(Exception):
    """The gateway refused or failed a model call."""


def judge_model_of(settings: Settings) -> str:
    """The judge's model: the setting, else the deployment's `strong` or `standard` profile."""
    profiles = settings.default_model_profiles
    return (
        settings.conversation_judge_model
        or profiles.get("strong")
        or profiles.get("standard")
        or "platform/standard"
    )


def player_model_of(spec: AgentSpec, settings: Settings) -> str:
    """The model the version answers on: its own, unless it names a profile; then the deployment's."""
    if spec.model and not spec.model.startswith("profile:"):
        return spec.model
    return settings.default_model_profiles.get("standard") or "platform/standard"


def gateway_complete(
    base_url: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> Complete:
    """One chat completion on the gateway, not streamed; a refusal raises `ModelRefusedError`."""

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as http:
            reply = await http.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
        if reply.status_code != 200:
            raise ModelRefusedError(f"the gateway answered {reply.status_code}: {reply.text[:200]}")
        parsed = reply.json()
        if not isinstance(parsed, dict):
            raise ModelRefusedError("the gateway answered something that is not a completion")
        return parsed

    return complete


async def _spec_of(session: AsyncSession, agent: Agent, version: int) -> AgentSpec:
    row = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise CannotPlayError(f"agent `{agent.slug}` has no version {version}")
    return AgentSpec.model_validate(row.spec)


async def default_player(
    session: AsyncSession,
    row: AssistantInstallation,
    name: str,
    suite: dict[str, Any],
    agent: Agent,
    version: int,
) -> SuiteResult | None:
    """Plays `suite` on the gateway for `version` of `agent`, or raises `CannotPlayError`."""
    from .agent_sessions import _OVERRIDES

    settings = get_settings()
    if not settings.gateway_master_key:
        raise CannotPlayError("no model is configured: the gateway has no key, so the suite cannot be played")
    if await org_brings_its_gateway(session, agent.org_id):
        raise CannotPlayError("the organisation brings its own gateway, which the platform has no key for")
    spec = await _spec_of(session, agent, version)
    transport = _OVERRIDES.get("transport")
    complete = gateway_complete(settings.gateway_url, settings.gateway_master_key, transport=transport)
    model = player_model_of(spec, settings)
    names = sorted({tool for case in suite.get("cases") or [] for _, tool in tools_named(case)})
    return await play_suite(
        suite,
        instructions=spec.instructions,
        tools=loop_tools(names),
        complete_for=lambda case: complete,
        judge=judge_with(complete, judge_model_of(settings)),
        model=model,
    )
