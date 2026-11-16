# SPDX-License-Identifier: Apache-2.0
"""An assistant's instructions, rendered for its conversation (S63-28, ADR 0133 point 3, ADR 0136 point 5).

The registry keeps the agent's instructions as a template; a conversation of an installation sends them
rendered, with two variables:

- `config`: the installation's values over the defaults of the manifest's `config_schema`;
- `preferences`: the caller's profile memory of the assistant (its `scope: user, kind: profile` spaces,
  merged in the manifest's order); empty when there is none, or when the policy says `memory.enabled: false`.

A plugin's internal agent carries the work item's context before its own instructions (`agent_catalogue`): a
conversation has no work item, so that block is left out.

A task run of the installation (ADR 0137) renders the same two variables through the orchestrator
(`task_instruction_values`, #1784), with the run's own variables, the work item's context included.

They are rendered when the conversation opens, and again when an idle conversation is resumed: an
installation whose config changed meanwhile reaches the conversations already open at their next resume.
Instructions that do not render refuse the conversation (409): the template never reaches the model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from choregos_core.agent_catalogue import without_context
from choregos_core.instructions import render_conversation_instructions
from jinja2 import TemplateError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantInstallation, Project, WorkItem
from ..errors import ApiError
from . import assistants

if TYPE_CHECKING:
    from .assistant_memory import Spaces


def config_values(document: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    """The installation's config over the `default` of each top-level property of `config_schema`."""
    properties = ((document or {}).get("config_schema") or {}).get("properties") or {}
    defaults = {
        str(key): spec["default"]
        for key, spec in properties.items()
        if isinstance(spec, dict) and "default" in spec
    }
    return {**defaults, **(config or {})}


async def preferences_of(session: AsyncSession, record: AgentSessionRecord) -> dict[str, Any]:
    """The caller's profile memory of the assistant: its `user` profile spaces' active documents."""
    from .assistant_memory import conversation_spaces

    return await _profiles(session, await conversation_spaces(session, record))


async def task_instruction_values(session: AsyncSession, item: WorkItem) -> dict[str, Any] | None:
    """`config` and `preferences` of a task run of an installation (#1784, ADR 0137): the installation's
    values over its defaults, and the subscriber's profile memory (the owner of the personal space the task
    runs in), empty under `memory.enabled: false`. `None` for any work item that is not a task's."""
    from choregos_core.policy import PolicyEngine

    from .assistant_memory import spaces_of
    from .definitions import effective_policy

    task = (item.documents or {}).get("task")
    if not isinstance(task, dict) or not task.get("installation_id"):
        return None
    installation = await session.get(AssistantInstallation, str(task["installation_id"]))
    space = await session.get(Project, item.project_id)
    if installation is None or space is None or not space.owner_user_id:
        return None
    document = await assistants.manifest(session, installation)
    spaces = await spaces_of(session, installation, space.owner_user_id)
    if spaces is not None and not PolicyEngine(await effective_policy(session, space.id)).memory_enabled():
        spaces = None
    return {
        "config": config_values(document, dict(installation.config or {})),
        "preferences": await _profiles(session, spaces),
    }


async def _profiles(session: AsyncSession, spaces: Spaces | None) -> dict[str, Any]:
    """The active documents of the `user` profile spaces, merged in the manifest's order."""
    from .assistant_memory import active_profile, find_store, profiles_of

    if spaces is None:  # no memory declared, or `memory.enabled: false`
        return {}
    merged: dict[str, Any] = {}
    for decl in spaces.decls.values():
        if decl.get("scope") != "user" or decl.get("kind") != "profile":
            continue
        store = await find_store(session, spaces, decl, None)
        current = active_profile(await profiles_of(session, store)) if store is not None else None
        if current is not None and isinstance(current.document, dict):
            merged.update(current.document)
    return merged


async def render_for(session: AsyncSession, record: AgentSessionRecord, source: str) -> str:
    """`source` rendered with the conversation's installation config and its caller's preferences."""
    installation = await session.get(AssistantInstallation, record.installation_id)
    if installation is None:
        return source
    document = await assistants.manifest(session, installation)
    config = config_values(document, dict(installation.config or {}))
    preferences = await preferences_of(session, record)
    try:
        return render_conversation_instructions(
            without_context(source), config=config, preferences=preferences
        )
    except (TemplateError, TypeError, ValueError) as error:
        raise ApiError(
            409,
            "Assistant unavailable",
            f"the instructions of `{installation.slug}` do not render ({type(error).__name__}: {error})",
        ) from error


async def render_instructions_of(session: AsyncSession, record: AgentSessionRecord, source: str) -> bool:
    """The conversation's first message, the agent's instructions, rendered from `source` (the agent
    revision's template). Returns whether the message changed. A conversation of no installation, or whose
    agent has no instructions, is left as it is."""
    if record.installation_id is None or not source:
        return False
    messages = list(record.messages or [])
    if not messages or messages[0].get("role") != "system":
        return False
    rendered = await render_for(session, record, source)
    current = str(messages[0].get("content") or "")
    from .conversation_knowledge import CITATION_RULE

    if CITATION_RULE in current:  # added to the first message by an earlier turn that read knowledge
        rendered = f"{rendered}\n\n{CITATION_RULE}"
    if current == rendered:
        return False
    messages[0] = {**messages[0], "content": rendered}
    record.messages = messages
    return True


__all__ = [
    "config_values",
    "preferences_of",
    "render_for",
    "render_instructions_of",
    "task_instruction_values",
]
