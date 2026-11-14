# SPDX-License-Identifier: Apache-2.0
"""`choregos ask`: one turn of a durable conversation with an assistant, streamed, scriptable (ADR 0154 §3).

The terminal is a surface of the platform, not an agent: the turn runs on the platform, under the assistant's
installation, its caps and its audit; nothing runs here. The conversation is the durable one of ADR 0134, so a
conversation opened here is resumed in the console, through the MCP door or here again (`--conversation`,
`--continue`).

Standard output carries the answer and nothing else, so `choregos ask "…" > answer.md` keeps only it. What a
person reads beside it goes to standard error: the conversation's id, the tokens and the money, and every
write the assistant proposed during this turn with where to confirm it. A write is never confirmed from here:
the confirmation is the console's until a developer-surface session exists (ADR 0154 §6-7).
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer

from . import theme
from .client import ApiError, Client, NoOrganisationError

#: The answer's statuses a turn ends with (`AgentResponse.status`).
COMPLETED = "completed"
#: The conversation statuses a new message continues (`ConversationSummary.status`): `idle` is reopened.
RESUMABLE = frozenset({"open", "idle"})


class AskError(RuntimeError):
    """A readable reason the turn was not sent."""


def ask(
    message: Annotated[
        str | None,
        typer.Argument(help="what to say; `-` or nothing reads it from standard input"),
    ] = None,
    assistant: Annotated[
        str | None,
        typer.Option("--assistant", "-a", help="the assistant's slug; the only one ready for you otherwise"),
    ] = None,
    conversation: Annotated[
        str | None, typer.Option("--conversation", "-c", help="continue this conversation (its id)")
    ] = None,
    resume: Annotated[
        bool, typer.Option("--continue", help="continue the assistant's most recent conversation")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="print one JSON object once the turn ended, nothing while it runs")
    ] = False,
    org: Annotated[
        str | None, typer.Option("--org", help="the organisation; the profile's otherwise")
    ] = None,
) -> None:
    """Ask an assistant, as one turn of a conversation the platform keeps.

    The answer streams to standard output; the conversation's id, its cost and the writes the assistant
    proposed (with where to confirm them) go to standard error. Exit code 0 when the turn completed, 1
    otherwise.
    """
    from . import main

    err = theme.console(stderr=True)
    text = _message(message)
    if conversation and resume:
        main.fail("`--conversation` and `--continue` are two ways to choose one conversation: give one")
    api = main.client()
    try:
        cid = conversation or _conversation(api, org, assistant, resume)
        before = set() if conversation is None and not resume else {a["id"] for a in _actions(api, cid)}
    except (AskError, NoOrganisationError) as exc:
        main.fail(str(exc))
        return
    streamed, final = _turn(api, cid, text, echo=not as_json)
    answer = streamed or str((final or {}).get("output_text") or "")
    if not as_json and answer and not streamed:
        sys.stdout.write(answer)
    if not as_json and answer and not answer.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    proposed = [a for a in _actions(api, cid) if a["id"] not in before and a.get("status") == "proposed"]
    pending = [
        {"id": a["id"], "summary": a.get("summary") or "", "confirm_url": confirm_url(api, cid, a["id"])}
        for a in proposed
    ]
    status = str((final or {}).get("status") or "failed")
    if as_json:
        document = {
            "conversation_id": cid,
            "status": status,
            "text": answer,
            "pending_actions": pending,
            "usage": (final or {}).get("usage") or {},
            "error": (final or {}).get("error"),
        }
        sys.stdout.write(json.dumps(document, ensure_ascii=False) + "\n")
    else:
        _footer(err, api, cid, final or {}, pending)
    if status != COMPLETED:
        raise typer.Exit(1)


def _message(message: str | None) -> str:
    """The argument, or standard input for `-` or nothing; an empty message is refused before any call."""
    from . import main

    text = sys.stdin.read() if message in (None, "-") else str(message)
    if not text.strip():
        main.fail("nothing to ask: give a message, or pipe one into `choregos ask -`")
    return text


def _conversation(api: Client, org: str | None, assistant: str | None, resume: bool) -> str:
    """The conversation to talk in: the assistant's most recent one with `--continue`, a new one otherwise."""
    organisation = api.profile.organisation(org)
    slug = _assistant(api, organisation, assistant)
    if resume:
        page = api.get("/me/conversations", params={"limit": 50})
        for summary in page.get("items") or []:
            if (
                summary.get("org") == organisation
                and summary.get("assistant") == slug
                and summary.get("status") in RESUMABLE
            ):
                return str(summary["id"])
    opened = api.post(f"/me/assistants/{organisation}/{slug}/conversations", json={})
    return str(opened["id"])


def _assistant(api: Client, organisation: str, chosen: str | None) -> str:
    """The assistant asked for, if the person's audience holds it and it is ready; else the only ready one."""
    mine = [a for a in api.get("/me/assistants").get("items") or [] if a.get("org") == organisation]
    if chosen:
        found = next((a for a in mine if a.get("slug") == chosen), None)
        if found is None:
            raise AskError(f"no assistant `{chosen}` for you in {organisation} (`choregos ask --help`)")
        if found.get("status") != "ready":
            raise AskError(f"the assistant `{chosen}` is {found.get('status')}: its owners must complete it")
        return chosen
    ready = sorted(str(a["slug"]) for a in mine if a.get("status") == "ready")
    if len(ready) == 1:
        return ready[0]
    if not ready:
        raise AskError(f"no assistant is ready for you in {organisation}")
    names = ", ".join(ready)
    raise AskError(
        f"several assistants are ready for you in {organisation}: choose one with --assistant ({names})"
    )


def _actions(api: Client, cid: str) -> list[dict[str, Any]]:
    return list(api.get(f"/me/conversations/{cid}/actions").get("items") or [])


def _turn(api: Client, cid: str, text: str, *, echo: bool) -> tuple[str, dict[str, Any] | None]:
    """Send the message and follow the turn: each delta written as it comes. Leaving the stream (Ctrl-C)
    cancels the turn on the platform, which books what it spent (S63-04b)."""
    from . import main

    chunks: list[str] = []
    final: dict[str, Any] | None = None
    body = {"input": text, "stream": True}
    try:
        for event, data in api.stream_events("POST", f"/me/conversations/{cid}/messages", json=body):
            if event == "response.output_text.delta":
                chunk = str(data.get("delta") or "")
                chunks.append(chunk)
                if echo:
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            elif event in ("response.completed", "response.failed"):
                final = data
            elif event == "error":
                detail = data.get("detail") or data.get("text") or "the turn was refused"
                final = {
                    "status": "failed",
                    "error": {"code": str(data.get("status") or "error"), "message": str(detail)},
                }
    except ApiError as exc:
        main.fail(str(exc))
    except KeyboardInterrupt:
        theme.console(stderr=True).print(
            "\ninterrupted: the platform cancels the turn and books what it spent"
        )
        raise typer.Exit(130) from None
    return "".join(chunks), final


def confirm_url(api: Client, cid: str, action_id: str) -> str:
    """Where the person confirms a proposed write: the console's conversation page, as the MCP door's
    `confirm_url` (`services/conversation_actions.confirm_url`). The CLI knows the instance's address, which
    is the console's when the CLI was installed from it (S62-08); the REST answer does not carry the link yet
    (ADR 0154 G1)."""
    return f"{api.profile.api_url.rstrip('/')}/conversations/{cid}?action={action_id}"


def _footer(err: Any, api: Client, cid: str, final: dict[str, Any], pending: list[dict[str, str]]) -> None:
    """What a person reads beside the answer, on standard error."""
    usage = final.get("usage") or {}
    error = final.get("error") or {}
    if final.get("status") != COMPLETED:
        reason = error.get("message") or "the turn did not complete"
        err.print(theme.badge(theme.status_of("failed"), err), f"{reason} ({error.get('code') or 'failed'})")
    summary = api.get(f"/me/conversations/{cid}")
    err.print(
        f"conversation {cid} · {int(usage.get('input_tokens') or 0)} in / "
        f"{int(usage.get('output_tokens') or 0)} out tokens · "
        f"${float(summary.get('cost_usd') or 0):.4f} spent in this conversation",
        markup=False,
    )
    for action in pending:
        err.print(theme.badge(theme.WAITING, err), action["summary"], markup=False)
        err.print(f"  confirm or refuse: {action['confirm_url']}", markup=False, soft_wrap=True)
