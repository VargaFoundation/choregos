# SPDX-License-Identifier: Apache-2.0
"""The MCP door talks to assistants (S63-11, ADR 0139; spec A6).

Two doors, one service:
- `/mcp` announces `list_assistants`, `ask_assistant` and `get_conversation` when the caller is in the
  audience of at least one ready installation;
- `/mcp/assistants/{org}:{installation}` is one installation as its own connector: `ask`,
  `get_conversation`, and the manifest's `surfaces.mcp.prompts` as MCP prompts.

A conversation opened here is the `/me/conversations` object of S63-04, in the caller's personal space
(`open_conversation`): `mcp:read` is enough to converse, and no tool decides (ADR 0030) -- a proposal is
confirmed in the console, which the answer links to.

A turn that outlasts `mcp_assistant_wait_s` answers `working`. The turn runs as an asyncio task of its own,
with its own session, so it survives the request; `get_conversation` reads it back from that task, from the
outcome it left, or -- for a turn another process runs -- from the journal (`turn.started` without its
`turn.ended`, younger than a turn key's life).

The audience test is the console's: `assistant_access.holds(principal, Permission.ASSISTANT_USE, org, row)`,
the organisation membership and one of the installation's `audience_groups`; an empty audience holds nobody.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import select

from ..config import get_settings
from ..db.models import (
    AgentSessionEvent,
    AgentSessionRecord,
    AssistantInstallation,
    Organization,
)
from ..db.session import name_the_user, release_connection, session_scope
from ..errors import ApiError
from ..logging import get_logger
from ..rbac import Permission
from ..services import assistants as installations
from ..services.assistant_access import holds
from ..services.conversation_signals import signals_of
from ..services.conversations import TURN_KEY_TTL_S, converse, open_conversation

if TYPE_CHECKING:
    from .tools import Context, Tool

log = get_logger(__name__)

WORKING = "working"
COMPLETED = "completed"
FAILED = "failed"
#: Outcomes kept in memory for `get_conversation` (a turn's failure is not in the transcript).
_KEPT_OUTCOMES = 256
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class Door:
    """An installation the caller may talk to, with its organisation's slug."""

    installation: AssistantInstallation
    org: str

    @property
    def reference(self) -> str:
        return f"{self.org}:{self.installation.slug}"


async def reachable(ctx: Context) -> list[Door]:
    """The ready installations whose audience holds the caller, in the organisations they belong to."""
    principal = ctx.caller.principal
    if not principal.org_roles:
        return []
    rows = await ctx.session.execute(
        select(AssistantInstallation, Organization.slug)
        .join(Organization, Organization.id == AssistantInstallation.org_id)
        .where(
            Organization.slug.in_(sorted(principal.org_roles)),
            AssistantInstallation.status == installations.READY,
        )
        .order_by(Organization.slug, AssistantInstallation.slug)
    )
    return [Door(row, org) for row, org in rows.all() if holds(principal, Permission.ASSISTANT_USE, org, row)]


async def door_of(ctx: Context, reference: str) -> Door | None:
    """`org:slug` -- or a bare slug when it is unambiguous -- among the installations the caller may use."""
    found = await reachable(ctx)
    exact = [d for d in found if d.reference == reference]
    if exact:
        return exact[0]
    bare = [d for d in found if d.installation.slug == reference]
    return bare[0] if len(bare) == 1 else None


async def resolve_connector(ctx: Context, reference: str) -> Door | None:
    """The installation behind `/mcp/assistants/{org}:{installation}`; `None` outside its audience."""
    org, _, slug = reference.partition(":")
    return next((d for d in await reachable(ctx) if d.org == org and d.installation.slug == slug), None)


# -- the answer ------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Outcome:
    status: str
    text: str


#: A live turn per conversation, and the last outcome of those that ended (this process only).
_TURNS: dict[str, asyncio.Task[Outcome]] = {}
_OUTCOMES: dict[str, Outcome] = {}


def _answer(
    conversation_id: str,
    status: str,
    text: str,
    pending: list[dict[str, Any]] | None = None,
    signals: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The door's answer. `citations` are what the knowledge tools returned in the turn, `pending_actions`
    the proposals of the inline writes, `missing_accounts` the `account_not_connected` of the turn's tools."""
    citations, accounts = signals or ([], [])
    return {
        "conversation_id": conversation_id,
        "status": status,
        "text": text,
        "citations": citations,
        "pending_actions": pending or [],
        "missing_accounts": accounts,
    }


def _spoken(answer: dict[str, Any]) -> str:
    if answer["status"] == WORKING:
        return (
            "The assistant is still working on this turn. Call get_conversation with "
            f"conversation_id `{answer['conversation_id']}` again in a few seconds to read its answer."
        )
    text = str(answer["text"])
    for action in answer["pending_actions"]:
        text += f"\nTo confirm: {action['summary']} ({action['confirm_url']})"
    for account in answer["missing_accounts"]:
        text += f"\nConnect your {account['need']} account first: {account['connect_url']}"
    return text


async def _run_turn(org: str, user_id: str, conversation_id: str, message: str) -> Outcome:
    """One turn, in a session of its own scoped like the caller's request (their organisation and them)."""
    try:
        async with session_scope(orgs=[org]) as session:
            await name_the_user(session, user_id)
            record = await session.get(AgentSessionRecord, conversation_id)
            if record is None:
                return Outcome(FAILED, "the conversation no longer exists")
            answer = await converse(session, record, message)
        if answer.status == "failed":
            return Outcome(FAILED, answer.error.message if answer.error else "the turn failed")
        return Outcome(COMPLETED, answer.output_text)
    except ApiError as refused:
        return Outcome(FAILED, refused.detail or refused.title)
    except Exception as failure:
        log.warning("mcp.assistant_turn_failed", conversation=conversation_id, error=type(failure).__name__)
        return Outcome(FAILED, "the turn failed")


def _start(org: str, user_id: str, conversation_id: str, message: str) -> asyncio.Task[Outcome]:
    task = asyncio.create_task(_run_turn(org, user_id, conversation_id, message))
    _TURNS[conversation_id] = task

    def finished(done: asyncio.Task[Outcome]) -> None:
        if _TURNS.get(conversation_id) is done:
            del _TURNS[conversation_id]
        _OUTCOMES[conversation_id] = done.result() if not done.cancelled() else Outcome(FAILED, "cancelled")
        while len(_OUTCOMES) > _KEPT_OUTCOMES:
            del _OUTCOMES[next(iter(_OUTCOMES))]

    task.add_done_callback(finished)
    return task


async def _owned(ctx: Context, conversation_id: object, door: Door | None) -> AgentSessionRecord:
    from .tools import ToolRefused

    record = (
        await ctx.session.get(AgentSessionRecord, conversation_id)
        if isinstance(conversation_id, str)
        else None
    )
    if (
        record is None
        or record.kind != "conversation"
        or record.user_id != ctx.caller.principal.user_id
        or record.installation_id is None
        or (door is not None and record.installation_id != door.installation.id)
    ):
        raise ToolRefused(f"conversation `{conversation_id}` not found")
    ctx.target_org_id = record.org_id
    return record


async def _turn_in_flight(ctx: Context, record: AgentSessionRecord) -> bool:
    """A turn another process runs: started, not ended, and young enough for its key to be alive."""
    events = (
        await ctx.session.execute(
            select(AgentSessionEvent.type, AgentSessionEvent.ts).where(
                AgentSessionEvent.session_id == record.id,
                AgentSessionEvent.type.in_(["turn.started", "turn.ended"]),
            )
        )
    ).all()
    started = [ts for type_, ts in events if type_ == "turn.started"]
    ended = [1 for type_, _ in events if type_ == "turn.ended"]
    if len(started) <= len(ended):
        return False
    return utcnow() - aware(max(started)) < timedelta(seconds=TURN_KEY_TTL_S)  # type: ignore[operator]


def _last_answer(record: AgentSessionRecord) -> str | None:
    """What the assistant said last, when it said it after the last thing the person said."""
    for message in reversed(record.messages or []):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
        if message.get("role") == "user":
            return None
    return None


async def _pending(ctx: Context, conversation_id: str) -> list[dict[str, Any]]:
    """The writes the assistant proposed and the person has not decided, each with the console link where
    they decide. The door lists them; it never executes one (ADR 0030)."""
    from ..services.conversation_actions import actions_of, confirm_url

    return [
        {"id": a.id, "summary": a.summary, "confirm_url": confirm_url(conversation_id, a.id)}
        for a in await actions_of(ctx.session, conversation_id, status="proposed")
    ]


async def _read(ctx: Context, record: AgentSessionRecord) -> dict[str, Any]:
    pending = await _pending(ctx, record.id)
    signals = await signals_of(ctx.session, record.id)
    if (task := _TURNS.get(record.id)) is not None and not task.done():
        return _answer(record.id, WORKING, "", pending, signals)
    if (kept := _OUTCOMES.get(record.id)) is not None:
        return _answer(record.id, kept.status, kept.text, pending, signals)
    if await _turn_in_flight(ctx, record):
        return _answer(record.id, WORKING, "", pending, signals)
    said = _last_answer(record)
    if said is None and record.turns:
        return _answer(record.id, FAILED, "the last turn left no answer", pending, signals)
    return _answer(record.id, COMPLETED, said or "", pending, signals)


# -- tools -----------------------------------------------------------------------------------------------

ASK_DESCRIPTION = (
    "Send a message to the assistant and read its answer. Without conversation_id a conversation is opened "
    "in your personal space; with one, the conversation goes on. The result carries `status`: when it is "
    "`working` the turn is not finished: call get_conversation with the same conversation_id again until "
    "the status is `completed`. `pending_actions` are proposals the assistant made: you confirm them in the "
    "console through `confirm_url`, never here. `missing_accounts` are accounts to connect first."
)
GET_DESCRIPTION = (
    "Read the state of a conversation with an assistant: `working` while a turn runs (call again in a few "
    "seconds), `completed` with the assistant's last answer, or `failed`."
)


async def _ask(ctx: Context, door: Door, arguments: dict[str, Any]) -> tuple[str, Any]:
    from .tools import ToolRefused

    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ToolRefused("argument `message` missing: what to say to the assistant")
    principal = ctx.caller.principal
    ctx.target_org_id = door.installation.org_id
    if arguments.get("conversation_id") is not None:
        record = await _owned(ctx, arguments["conversation_id"], door)
        live = _TURNS.get(record.id)
        if live is not None and not live.done():
            raise ToolRefused(
                f"a turn is still working in `{record.id}`: call get_conversation until it is completed"
            )
        _OUTCOMES.pop(record.id, None)
    else:
        manifest = await installations.manifest(ctx.session, door.installation)
        organisation = await ctx.session.get(Organization, door.installation.org_id)
        if manifest is None or organisation is None:
            raise ToolRefused(f"assistant `{door.reference}` is not available")
        context = arguments.get("context")
        try:
            record = await open_conversation(
                ctx.session,
                principal,
                organisation,
                str(manifest["agent"]),
                context=context if isinstance(context, dict) else None,
                installation_id=door.installation.id,
            )
        except ApiError as refused:
            raise ToolRefused(refused.detail or refused.title) from refused
    conversation_id = record.id
    # The turn reads the conversation from a session of its own: what this request wrote is committed first.
    await release_connection(ctx.session)
    task = _start(door.org, principal.user_id, conversation_id, message)
    await asyncio.wait({task}, timeout=get_settings().mcp_assistant_wait_s)
    outcome = task.result() if task.done() else None
    pending = await _pending(ctx, conversation_id)
    signals = await signals_of(ctx.session, conversation_id)
    answer = (
        _answer(conversation_id, outcome.status, outcome.text, pending, signals)
        if outcome is not None
        else _answer(conversation_id, WORKING, "", pending, signals)
    )
    return _spoken(answer), answer


async def _get(ctx: Context, door: Door | None, arguments: dict[str, Any]) -> tuple[str, Any]:
    record = await _owned(ctx, arguments.get("conversation_id"), door)
    answer = await _read(ctx, record)
    return _spoken(answer), answer


def _conversation_schema() -> dict[str, Any]:
    return {"conversation_id": {"type": "string", "description": "The conversation, as `ask` answered it."}}


def _tool(name: str, title: str, description: str, schema: dict[str, Any], run: Any) -> Tool:
    from .tools import Tool

    return Tool(name, title, description, schema, False, run)


async def assistant_tools(ctx: Context) -> list[Tool]:
    """The assistants' tools of this context: those of the connector's installation, or the door's three."""
    if ctx.assistant is not None:
        pinned = ctx.assistant

        async def ask(c: Context, a: dict[str, Any]) -> tuple[str, Any]:
            return await _ask(c, pinned, a)

        async def get(c: Context, a: dict[str, Any]) -> tuple[str, Any]:
            return await _get(c, pinned, a)

        return [
            _tool(
                "ask",
                "Ask the assistant",
                ASK_DESCRIPTION,
                _object(
                    {
                        "message": {"type": "string", "minLength": 1},
                        "conversation_id": {
                            "type": "string",
                            "description": "Continue this conversation instead of opening one.",
                        },
                        "context": {"type": "object", "description": "What the screen or the tool knows."},
                    },
                    ["message"],
                ),
                ask,
            ),
            _tool(
                "get_conversation",
                "Read a conversation",
                GET_DESCRIPTION,
                _object(_conversation_schema(), ["conversation_id"]),
                get,
            ),
        ]
    found = await reachable(ctx)
    if not found:
        return []

    async def list_(c: Context, _a: dict[str, Any]) -> tuple[str, Any]:
        return await _list(c, found)

    async def ask_any(c: Context, a: dict[str, Any]) -> tuple[str, Any]:
        from .tools import ToolRefused

        reference = a.get("assistant")
        door = await door_of(c, reference) if isinstance(reference, str) else None
        if door is None:
            raise ToolRefused(f"assistant `{reference}` not found: use `org:slug` from list_assistants")
        return await _ask(c, door, a)

    async def get_any(c: Context, a: dict[str, Any]) -> tuple[str, Any]:
        return await _get(c, None, a)

    return [
        _tool(
            "list_assistants",
            "List assistants",
            "List the assistants you may talk to, as `org:slug`, with what each one does.",
            _object({}, []),
            list_,
        ),
        _tool(
            "ask_assistant",
            "Ask an assistant",
            ASK_DESCRIPTION,
            _object(
                {
                    "assistant": {"type": "string", "description": "The assistant, as `org:slug`."},
                    "message": {"type": "string", "minLength": 1},
                    "conversation_id": {
                        "type": "string",
                        "description": "Continue this conversation instead of opening one.",
                    },
                    "context": {"type": "object", "description": "What the screen or the tool knows."},
                },
                ["assistant", "message"],
            ),
            ask_any,
        ),
        _tool(
            "get_conversation",
            "Read a conversation",
            GET_DESCRIPTION,
            _object(_conversation_schema(), ["conversation_id"]),
            get_any,
        ),
    ]


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


async def _list(ctx: Context, found: list[Door]) -> tuple[str, Any]:
    items: list[dict[str, Any]] = []
    for door in found:
        document = await installations.manifest(ctx.session, door.installation) or {}
        meta = document.get("metadata") or {}
        items.append(
            {
                "assistant": door.reference,
                "name": meta.get("display_name") or door.installation.assistant_name,
                "description": meta.get("description") or "",
                "connector_path": f"/mcp/assistants/{door.reference}",
            }
        )
    lines = [f"- {i['assistant']}: {i['name']} -- {i['description']}" for i in items]
    return "Assistants you may talk to:\n" + "\n".join(lines), {"assistants": items}


# -- prompts ---------------------------------------------------------------------------------------------


async def _prompts(ctx: Context) -> list[dict[str, Any]]:
    if ctx.assistant is None:
        return []
    document = await installations.manifest(ctx.session, ctx.assistant.installation) or {}
    prompts = ((document.get("surfaces") or {}).get("mcp") or {}).get("prompts") or []
    return [p for p in prompts if isinstance(p, dict) and p.get("name")]


def _argument(raw: Any) -> dict[str, Any]:
    spec = raw if isinstance(raw, dict) else {"name": str(raw)}
    out: dict[str, Any] = {"name": str(spec["name"]), "required": bool(spec.get("required", False))}
    if spec.get("description"):
        out["description"] = str(spec["description"])
    return out


async def prompt_list(ctx: Context) -> list[dict[str, Any]]:
    return [
        {
            "name": str(p["name"]),
            "description": str(p.get("description") or ""),
            "arguments": [_argument(a) for a in p.get("arguments") or []],
        }
        for p in await _prompts(ctx)
    ]


async def prompt_get(ctx: Context, params: dict[str, Any]) -> dict[str, Any] | str:
    """The rendered prompt, or the reason it cannot be (the caller answers -32602)."""
    prompt = next((p for p in await _prompts(ctx) if p["name"] == params.get("name")), None)
    if prompt is None:
        return f"Unknown prompt: {params.get('name')}"
    given = params.get("arguments") or {}
    if not isinstance(given, dict):
        return "arguments must be an object"
    declared = [_argument(a) for a in prompt.get("arguments") or []]
    missing = [a["name"] for a in declared if a["required"] and not str(given.get(a["name"], "")).strip()]
    if missing:
        return f"Missing required argument: {', '.join(missing)}"
    values = {a["name"]: str(given.get(a["name"], "")) for a in declared}
    text = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), str(prompt["message"]))
    return {
        "description": str(prompt.get("description") or ""),
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


__all__ = ["Door", "assistant_tools", "prompt_get", "prompt_list", "resolve_connector"]
