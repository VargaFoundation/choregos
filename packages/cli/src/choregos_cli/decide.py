# SPDX-License-Identifier: Apache-2.0
"""`choregos decide`: what waits for the person, decided from the terminal (ADR 0154 §7, ADR 0153).

Four kinds wait for a person (`GET /me/pending`): a write or a workflow start an assistant proposed in their
conversation, a governed action awaiting an approver, and a human step of a workflow. Each is decided on the
route that already decides it, **as the person**: a decision needs their session (`choregos login`), never a
pasted API token, and the API refuses one (`decision_requires_session`); the CLI does not try to second-guess
it, it relays the API's message and says how to sign in.

The rules of the gesture, kept here so the command and the workspace's decisions pane share them:

- the operation, its arguments and the source's diff (when it gave one) are shown **before** the gesture;
- ONE explicit gesture decides ONE action: the id is one argument, the verb is typed (`approve`, `reject`,
  `answer`), and there is no default, no `--yes`, no `--all`, no loop: a batch confirmation does not exist
  (`test_no_batch_confirmation_exists`);
- a rejection asks a reason, which the API audits where its route takes one.

This module holds no Textual. What an assistant or a source wrote is data: every control character is removed
before it reaches the screen, and it is drawn as plain text, never as markup.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import theme
from .client import ApiError, Client

KIND_LABEL = {
    "conversation_action": "write proposed by an assistant",
    "launch": "workflow start proposed by an assistant",
    "governed_action": "governed action awaiting approval",
    "human_step": "human step",
}
#: A value longer than this is cut, and says so: a screen is no place for a whole file.
MAX_VALUE = 1500
SIGN_IN = "sign in as yourself with `choregos login` (a pasted API token cannot decide)"
FRESH = "your sign-in is too old for this decision: run `choregos login --fresh`, then repeat it"

decide_app = typer.Typer(
    help="Decide what waits for you: confirm a write, approve an action, answer a step. One at a time."
)

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(value: Any) -> str:
    """Text for a screen: no control character, a proposal cannot move the cursor or recolour the terminal."""
    return _CONTROL.sub("", str(value))


# ───────────────────────────── what is shown before the gesture ─────────────────────────────


def detail_path(item: dict[str, Any]) -> str:
    """Where the item's own record is read: its operation, arguments and diff are not in the list."""
    kind = item["kind"]
    if kind in ("conversation_action", "launch"):
        return f"/me/conversations/{item['where']['conversation_id']}/actions"
    if kind == "governed_action":
        return str(item["decision"]["path"]).removesuffix("/decision")
    return f"/work-items/{item['where']['work_item_id']}"


def pick_detail(item: dict[str, Any], payload: Any) -> dict[str, Any] | None:
    """The record out of what `detail_path` answered; `None` when it is not waiting any more."""
    if not isinstance(payload, dict):
        return None
    if item["kind"] in ("conversation_action", "launch"):
        return next((a for a in payload.get("items") or [] if a.get("id") == item["id"]), None)
    if item["kind"] == "governed_action":
        return payload if payload.get("id") == item["id"] else None
    request = payload.get("pending_request")
    return request if isinstance(request, dict) and request.get("id") == item["id"] else None


def _value(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = clean(text)
    if len(text) > MAX_VALUE:
        return f"{text[:MAX_VALUE]}… ({len(text) - MAX_VALUE} more characters)"
    return text


def _pairs(mapping: dict[str, Any], indent: str = "  ") -> list[Text]:
    lines: list[Text] = []
    for key, value in mapping.items():
        shown = _value(value).split("\n")
        lines.append(Text(f"{indent}{clean(key)}: {shown[0]}"))
        lines.extend(Text(f"{indent}  {more}") for more in shown[1:])
    return lines


def _diff(diff: dict[str, Any]) -> list[Text]:
    """What the source's preview returned: a text diff is drawn with its `+`/`-`, anything else as pairs."""
    lines: list[Text] = []
    plain: dict[str, Any] = {}
    for key, value in diff.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(Text(f"  {clean(key)}:", style="bold"))
            for row in _value(value).split("\n"):
                style = "green" if row.startswith("+") else "red" if row.startswith("-") else ""
                lines.append(Text(f"    {row}", style=style))
        else:
            plain[key] = value
    return _pairs(plain) + lines


def describe(item: dict[str, Any], detail: dict[str, Any] | None) -> list[Text]:
    """The operation, its readable arguments and the diff when given: what a person reads before deciding."""
    lines = [Text(clean(KIND_LABEL.get(item["kind"], item["kind"])), style="bold"), Text(clean(item["what"]))]
    where = item.get("where") or {}
    place = " · ".join(
        clean(where[k]) for k in ("org", "project_slug", "work_item_key", "conversation_id") if where.get(k)
    )
    if place:
        lines.append(Text(f"in {place}", style="dim"))
    if detail is None:
        lines.append(Text("its record could not be read: it may not be waiting any more", style="yellow"))
        return lines
    kind = item["kind"]
    if kind == "conversation_action":
        lines.append(Text(f"operation: {clean(detail.get('need'))}.{clean(detail.get('operation'))}"))
    elif kind == "launch":
        target = detail.get("target") or {}
        lines.append(
            Text(
                f"launch: workflow {clean(target.get('workflow'))} in project {clean(target.get('project'))}"
            )
        )
    elif kind == "governed_action":
        lines.append(Text(f"operation: {clean(detail.get('kind'))} (from {clean(detail.get('origin'))})"))
        if detail.get("justification"):
            lines.append(Text(f"why: {_value(detail['justification'])}"))
    else:
        lines.append(Text(f"asks: {clean(detail.get('kind'))}"))
    arguments = (
        detail.get("arguments")
        if kind in ("conversation_action", "launch")
        else detail.get("params")
        if kind == "governed_action"
        else detail.get("payload")
    )
    if arguments:
        lines.append(Text("arguments:", style="bold"))
        lines.extend(_pairs(arguments))
    if kind == "governed_action" and detail.get("effects"):
        lines.append(Text("effects:", style="bold"))
        lines.extend(Text(f"  {_value(effect)}") for effect in detail["effects"])
    if detail.get("diff"):
        lines.append(Text("diff:", style="bold"))
        lines.extend(_diff(detail["diff"]))
    return lines


# ───────────────────────────── the gesture ─────────────────────────────


def verbs(item: dict[str, Any], detail: dict[str, Any] | None) -> list[str]:
    """What the person may do with it here. A task's form and a scope change's paths stay in the console."""
    if detail is None:
        return []
    if item["kind"] != "human_step":
        return ["approve", "reject"]
    return {"approval": ["approve", "reject"], "question": ["answer"]}.get(str(detail.get("kind")), [])


def request_for(
    item: dict[str, Any], verb: str, *, reason: str = "", answer: str = ""
) -> tuple[str, dict[str, Any] | None]:
    """The route and body of ONE decision on ONE item."""
    kind, path = item["kind"], str(item["decision"]["path"])
    if kind in ("conversation_action", "launch"):
        # A refusal has no body: the route takes no reason, so none is asked for it.
        return (path if verb == "approve" else path.removesuffix("/confirm") + "/refuse"), None
    if kind == "governed_action":
        return path, {"decision": verb, "reason": reason or None}
    body: dict[str, Any] = {"request_id": item["id"], "kind": "answer" if verb == "answer" else verb}
    if verb == "answer":
        body["answer"] = answer
    if reason:
        body["reason"] = reason
    return path, body


def asks_reason(item: dict[str, Any], verb: str) -> bool:
    """Whether a rejection asks one: whenever the route can keep it (a conversation's refusal cannot)."""
    return verb == "reject" and item["kind"] not in ("conversation_action", "launch")


def explain(exc: ApiError) -> list[str]:
    """What a refused decision says: the API's own message, then how to get past it when there is a way."""
    lines = [str(exc)]
    if exc.code == "decision_requires_session":
        lines.append(SIGN_IN)
    elif exc.code == "step_up_required":
        lines.append(FRESH)
    return lines


# ───────────────────────────── the commands ─────────────────────────────


def _api() -> Client:
    from . import main

    return main.TransportSafeClient()


def _fail(message: str) -> None:
    from . import main

    main.fail(message)


def _pending(api: Client) -> list[dict[str, Any]]:
    listed = api.get("/me/pending", params={"limit": 200})
    return list((listed or {}).get("items") or [])


def _find(api: Client, item_id: str) -> dict[str, Any]:
    for item in _pending(api):
        if item["id"] == item_id:
            return item
    _fail(f"nothing waits for you under {clean(item_id)}: `choregos decide list` shows what does")
    raise typer.Exit(1)  # unreachable: `fail` raises


def _show(item: dict[str, Any], detail: dict[str, Any] | None, out: Console) -> None:
    for line in describe(item, detail):
        out.print(line)
    out.print(Text(f"console: {clean(item['decision']['url'])}", style="dim"))


@decide_app.command("list")
def list_pending() -> None:
    """What waits for you across your organisations, newest first (`GET /me/pending`)."""
    api = _api()
    out = theme.console()
    try:
        items = _pending(api)
    except ApiError as exc:
        _fail(str(exc))
        return
    if not items:
        out.print("nothing waits for you")
        return
    table = Table("id", "kind", "what", "in", box=None, pad_edge=False)
    for item in items:
        where = item.get("where") or {}
        place = where.get("project_slug") or where.get("org") or ""
        table.add_row(
            Text(item["id"], no_wrap=True),
            clean(KIND_LABEL.get(item["kind"], item["kind"])),
            clean(item["what"]),
            clean(place),
        )
    out.print(table)
    out.print(Text("decide one: `choregos decide approve|reject|answer ID`", style="dim"))


@decide_app.command("show")
def show(item_id: Annotated[str, typer.Argument(metavar="ID", help="what waits, by its id")]) -> None:
    """Show what one thing asks, with its arguments and diff, and decide nothing."""
    api = _api()
    try:
        item = _find(api, item_id)
        detail = pick_detail(item, api.get(detail_path(item)))
    except ApiError as exc:
        _fail(str(exc))
        return
    _show(item, detail, theme.console())


@decide_app.command("approve")
def approve(
    item_id: Annotated[str, typer.Argument(metavar="ID", help="one thing, by its id")],
) -> None:
    """Confirm a write or a launch, approve a governed action or an approval step: one, after seeing it."""
    _decide("approve", item_id)


@decide_app.command("reject")
def reject(
    item_id: Annotated[str, typer.Argument(metavar="ID", help="one thing, by its id")],
    reason: Annotated[
        str | None, typer.Option("--reason", "-r", help="why; asked for when it is not given")
    ] = None,
) -> None:
    """Refuse a write or a launch, reject a governed action or an approval step: one, with a reason."""
    _decide("reject", item_id, reason=reason)


@decide_app.command("answer")
def answer(
    item_id: Annotated[str, typer.Argument(metavar="ID", help="a question of a workflow, by its id")],
    text: Annotated[
        str | None, typer.Option("--answer", "-a", help="the answer; asked for when it is not given")
    ] = None,
) -> None:
    """Answer a workflow's question."""
    _decide("answer", item_id, answer=text)


def _asked(prompt: str, given: str | None) -> str:
    """A required line: what was passed, else asked for; empty is refused, never defaulted."""
    value = (given if given is not None else typer.prompt(prompt, default="", show_default=False)).strip()
    if not value:
        _fail(f"{prompt.split(' (', maxsplit=1)[0]} is required: nothing was sent")
    return value


def _decide(verb: str, item_id: str, *, reason: str | None = None, answer: str | None = None) -> None:
    api = _api()
    out = theme.console()
    try:
        item = _find(api, item_id)
        detail = pick_detail(item, api.get(detail_path(item)))
    except ApiError as exc:
        _fail(str(exc))
        return
    _show(item, detail, out)
    allowed = verbs(item, detail)
    if verb not in allowed:
        out.print(
            Text(
                f"✗ `{verb}` does not apply here"
                + (
                    f": use {' or '.join(f'`{v}`' for v in allowed)}"
                    if allowed
                    else ": decide it in the console"
                ),
                style="red",
            )
        )
        raise typer.Exit(1)
    given = {"reason": "", "answer": ""}
    if verb == "answer":
        given["answer"] = _asked("answer (required)", answer)
    elif asks_reason(item, verb):
        given["reason"] = _asked("reason (required)", reason)
    # The gesture: the verb itself, typed. Nothing else, and not Enter alone, sends it.
    typed = typer.prompt(
        f'type "{verb}" to {verb} this, anything else leaves it waiting', default="", show_default=False
    )
    if typed.strip() != verb:
        out.print("left waiting: nothing was sent")
        raise typer.Exit(1)
    path, body = request_for(item, verb, **given)
    try:
        api.post(path, **({} if body is None else {"json": body}))
    except ApiError as exc:
        for line in explain(exc):
            out.print(Text(f"✗ {line}", style="red"))
        raise typer.Exit(1) from None
    out.print(Text(f"✓ {verb} sent: {clean(item['what'])}", style="green"))
