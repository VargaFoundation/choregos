# SPDX-License-Identifier: Apache-2.0
"""The workspace of `choregos chat` (ADR 0154 §2, §8, §9), drawn with Textual.

Five parts, from routes that exist on `main` (ADR 0154 §4):

- **Conversation** (left): a durable conversation of ADR 0134, streamed; ending the turn closes the stream,
  which cancels it on the platform (S63-04b).
- **Live tree** (right, blue): the same board and the same lines as `choregos activity`
  (`activity.Board`, `activity.group`, `activity.line`), kept live by the activity stream. `x` stops the run
or
  the work item under the cursor, always with a reason (ADR 0142 §2).
- **Decisions** (right, amber): read-only. What waits for the person and what can be read today: the
  organisation's governed actions awaiting approval and the current conversation's proposals. There is no
  list across projects and conversations yet (ADR 0154 G8), and deciding from the terminal is S67-10.
- **Elsewhere** (below): `/me/inbox` and the person's recent conversations.
- **Status line**: `acme · N running · — waiting for you · $X today` (`activity.status_line`).

The terminal's sixteen colours (`ansi_color`), no background painted, glyph and word before colour; `NO_COLOR`
gives the monochrome rendering. Nothing here runs a tool, a model or an agent on the person's machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from . import activity, theme
from .ask import confirm_link
from .client import ApiError, AsyncClient, Profile

#: Seconds before the activity stream is opened again after it ended or failed.
RECONNECT_S = 3.0
#: How many conversations the switcher and the "elsewhere" pane list.
CONVERSATIONS_SHOWN = 50
#: Under this many columns the panes are stacked.
NARROW = 100
DASH = activity.DASH
Key = tuple[str, ...]


def run(
    profile: Profile,
    organisation: str,
    *,
    assistant: str | None = None,
    conversation: str | None = None,
    colour: bool = True,
) -> None:
    """Open the workspace and return when the person quits it."""
    if not colour:
        os.environ["NO_COLOR"] = "1"  # Textual reads it as well: its renderer drops every colour
    api = AsyncClient(profile)
    Workspace(api, organisation, assistant=assistant, conversation=conversation).run()


def smoke(profile: Profile, organisation: str) -> str:
    """Start the workspace headless (no terminal), check its five parts are there, quit it: what the binary's
    smoke test (`tools/cli_binary/smoke.py`) runs where no person is, to prove Textual and its data files
    are inside the binary."""
    found: list[str] = []

    async def drive(pilot: Any) -> None:
        await pilot.pause()
        for part in ("#conversation", "#prompt", "#tree", "#decisions", "#elsewhere", "#status"):
            if pilot.app.query(part):
                found.append(part)
        pilot.app.exit()

    Workspace(AsyncClient(profile), organisation).run(headless=True, auto_pilot=drive)
    return f"workspace ok: {len(found)} parts ({' '.join(found)})"


# ──────────────────────────────── what the tree draws ────────────────────────────────


def run_steps(entry: dict[str, Any], out: Console) -> list[Text]:
    """The lines under an expanded run node.

    The basic node is all the platform's activity entry says today: how many events it recorded and the last
    one. The run's plan and tool calls are the journal's reduction (S67-03), which this hook shows once it is
    merged; nothing else in the workspace changes when it is filled.
    """
    lines: list[Text] = []
    steps = int(entry.get("steps") or 0)
    if steps:
        text = Text(f"{steps} events")
        if entry.get("last_event"):
            text.append(f" · last: {entry['last_event']}")
        lines.append(text)
    return lines


class FlightTree(Tree[dict[str, Any]]):
    """The live tree: Enter expands a node, `x` stops what is under the cursor."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("x", "stop", "Stop (asks why)")]

    def action_stop(self) -> None:
        app = self.app
        assert isinstance(app, Workspace)
        app.action_stop_selected()


class StopScreen(ModalScreen[str | None]):
    """A stop always asks why (ADR 0142 §2): an empty reason is not sent."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", "Keep it running")]

    def __init__(self, what: str) -> None:
        super().__init__()
        self.what = what

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(f"Stop {self.what}", style="bold"))
            yield Static("The reason is audited and shown on the work item.")
            yield Input(placeholder="reason (required)", id="reason", max_length=500)
            yield Static("", id="problem")
            yield Static("Enter stops it · Esc keeps it running")

    @on(Input.Submitted, "#reason")
    def submit(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        if not reason:
            self.query_one("#problem", Static).update(Text("✗ a stop needs a reason", style="red"))
            return
        self.dismiss(reason)


class PickScreen(ModalScreen[str | None]):
    """A list to choose one from: the person's conversations, the assistants ready for them."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, title: str, options: list[tuple[str, RenderableType]], empty: str) -> None:
        super().__init__()
        self.heading, self.options, self.empty = title, options, empty

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading, style="bold"))
            if self.options:
                yield OptionList(*(Option(label, id=key) for key, label in self.options), id="choices")
            else:
                yield Static(self.empty)
            yield Static("Enter chooses · Esc closes")

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


# ───────────────────────────────────── the app ─────────────────────────────────────


class Workspace(App[None]):
    TITLE = "choregos"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #title { height: 1; padding: 0 1; }
    #main { height: 1fr; }
    #left { width: 2fr; height: 100%; }
    #right { width: 3fr; height: 100%; }
    .-narrow #main { layout: vertical; }
    .-narrow #left, .-narrow #right { width: 100%; height: 1fr; }
    .-narrow #elsewhere { height: 4; }
    .-narrow #inbox { display: none; }
    #conversation { height: 1fr; border: round ansi_default; padding: 0 1; }
    #prompt { border: round ansi_default; height: 3; }
    #prompt:focus { border: round ansi_blue; }
    #tree { height: 2fr; border: round ansi_blue; }
    #tree > .tree--guides, #tree > .tree--guides-selected { color: ansi_blue; }
    #tree > .tree--cursor { text-style: reverse bold; background: ansi_default; }
    #tree:focus > .tree--cursor { text-style: reverse bold; background: ansi_default; }
    #decisions { height: 1fr; border: round ansi_yellow; padding: 0 1; }
    #elsewhere { height: 7; }
    #inbox, #recent { width: 1fr; height: 100%; border: round ansi_default; padding: 0 1; }
    #status { height: 1; padding: 0 1; }
    .message { margin: 0 0 1 0; }
    StopScreen, PickScreen { align: center middle; }
    #dialog { width: 70%; max-width: 90; height: auto; border: round ansi_default; padding: 1 2; }
    #choices { height: auto; max-height: 14; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel_turn", "Cancel the turn"),
        Binding("ctrl+o", "conversations", "Conversations"),
        Binding("ctrl+n", "new_conversation", "New"),
        Binding("ctrl+a", "assistants", "Assistant"),
        Binding("tab", "focus_next", "Next pane"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        api: AsyncClient,
        organisation: str,
        *,
        assistant: str | None = None,
        conversation: str | None = None,
    ) -> None:
        super().__init__(ansi_color=True)
        self.api = api
        self.organisation = organisation
        self.assistant = assistant
        self.conversation_id = conversation
        self.conversation: dict[str, Any] | None = None
        self.out = theme.console()
        self.board = activity.Board()
        self.today: float | None = None
        self.assistants: list[dict[str, Any]] = []
        self.conversations: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] | None = None
        self.inbox: list[dict[str, Any]] = []
        self.live = False
        self._user_open: dict[Key, bool] = {}
        self._tree_keys: list[Key] = []
        self._by_key: dict[Key, TreeNode[dict[str, Any]]] = {}
        self._turn_running = False

    # ───────────────────────────── layout ─────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield VerticalScroll(id="conversation")
                yield Input(placeholder="Say something · Enter sends", id="prompt")
            with Vertical(id="right"):
                yield FlightTree("", id="tree")
                yield VerticalScroll(Static(id="decisions-body"), id="decisions")
        with Horizontal(id="elsewhere"):
            yield Static(id="inbox")
            yield Static(id="recent")
        yield Static(id="status")
        yield Footer()

    def on_resize(self, event: Any) -> None:
        """Below a hundred columns the conversation and the right-hand panes are stacked, not side by side."""
        self.screen.set_class(event.size.width < NARROW, "-narrow")

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        tree = self.query_one(FlightTree)
        tree.show_root = False
        tree.border_title = "Live tree"
        self.query_one("#conversation").border_title = "Conversation"
        self.query_one("#decisions").border_title = "Decisions"
        self.query_one("#inbox").border_title = "Elsewhere · inbox"
        self.query_one("#recent").border_title = "Elsewhere · recent conversations"
        self.query_one("#prompt", Input).focus()
        self.set_interval(activity.REDRAW_EVERY_S, self.tick)
        self.run_worker(self.load(), group="load", exit_on_error=False)
        self.run_worker(self.follow_activity(), group="activity", exit_on_error=False)

    # ───────────────────────────── loading ─────────────────────────────

    async def _read(self, path: str, **params: Any) -> Any:
        """A read that may fail without taking the workspace down: `None`, and a line in the status."""
        try:
            return await self.api.get(path, params=params or None)
        except (ApiError, httpx.TransportError) as exc:
            self.notify(str(exc) or type(exc).__name__, title=path, severity="warning")
            return None

    async def load(self) -> None:
        org = self.organisation
        assistants, conversations, inbox, today = await asyncio.gather(
            self._read("/me/assistants"),
            self._read("/me/conversations", limit=CONVERSATIONS_SHOWN),
            self._read("/me/inbox", limit=20),
            self._spend(),
        )
        self.assistants = [a for a in (assistants or {}).get("items") or [] if a.get("org") == org]
        self.conversations = [
            c for c in (conversations or {}).get("items") or [] if c.get("org") in (org, None)
        ]
        self.inbox = list((inbox or {}).get("items") or [])
        self.today = today
        if self.conversation_id:
            await self.open_conversation(self.conversation_id)
        else:
            self.pick_assistant_quietly()
            self.show_welcome()
        await self.refresh_decisions()
        self.redraw()

    async def _spend(self) -> float | None:
        day = datetime.now(UTC).date().isoformat()
        try:
            report = await self.api.get(
                f"/orgs/{self.organisation}/costs", params={"since": day, "until": day}
            )
        except (ApiError, httpx.TransportError):
            return None  # a project member may not read it: "—", never 0
        value = ((report or {}).get("total") or {}).get("cost_usd")
        return float(value) if isinstance(value, int | float) else None

    async def refresh_decisions(self) -> None:
        """What the platform lists for the person today: the organisation's governed actions awaiting
        approval, and this conversation's proposals."""
        try:
            actions = await self.api.get(
                f"/orgs/{self.organisation}/actions", params={"status": "pending_approval"}
            )
            self.pending = list(actions or [])
        except (ApiError, httpx.TransportError):
            self.pending = None
        if self.conversation_id:
            listed = await self._read(f"/me/conversations/{self.conversation_id}/actions")
            self.proposals = [a for a in (listed or {}).get("items") or [] if a.get("status") == "proposed"]
        else:
            self.proposals = []
        self.draw_decisions()

    async def follow_activity(self) -> None:
        """The board from the list, then from the stream; opened again when it ends."""
        path = f"/orgs/{self.organisation}/activity"
        while True:
            try:
                self.board = activity.Board.of(await self.api.get(path))
                self.live = True
                self.redraw()
                async for event, entry in self.api.stream_events("GET", f"{path}/stream"):
                    self.board.apply(event, entry)
                    self.redraw()
            except (ApiError, httpx.TransportError):
                pass
            self.live = False
            self.redraw()
            await asyncio.sleep(RECONNECT_S)

    # ───────────────────────────── drawing ─────────────────────────────

    def tick(self) -> None:
        """Elapsed times move without an event."""
        self.draw_tree()

    def redraw(self) -> None:
        self.draw_title()
        self.draw_tree()
        self.draw_decisions()
        self.draw_elsewhere()
        self.draw_status()

    def draw_title(self) -> None:
        text = Text("choregos", style="bold")
        text.append(f" · {self.organisation}")
        if self.assistant:
            text.append(" · ")
            text.append(self.assistant, style=theme.hue_style(self.assistant))
        if self.conversation and self.conversation.get("title"):
            text.append(f" · {self.conversation['title']}")
        self.query_one("#title", Static).update(text)

    def draw_status(self) -> None:
        status = activity.status_line(self.board, self.organisation, self.today)
        if not self.live:
            status.append(" · ")
            status.append_text(theme.badge(theme.QUEUED, self.out))
            status.append(" activity stream reconnecting")
        self.query_one("#status", Static).update(status)

    def _tree_shape(self) -> tuple[list[Key], list[tuple[Key, Key | None, Text, dict[str, Any]]]]:
        """The nodes the tree shows, parents first: `(key, parent, label, data)`."""
        now = activity.clock()
        rows: list[tuple[Key, Key | None, Text, dict[str, Any]]] = []

        def section(prefix: str, entries: list[dict[str, Any]], title: Text | None) -> None:
            grouped = activity.group(entries)
            top: Key | None = None
            if title is not None:
                top = (prefix,)
                rows.append((top, None, title, {"kind": "section"}))
            for project, items in grouped.projects.items():
                pkey: Key = (prefix, "project", project)
                rows.append(
                    (pkey, top, Text(project, style=f"bold {theme.hue_style(project)}"), {"kind": "project"})
                )
                for item, runs in items.items():
                    ikey: Key = (prefix, "item", project, item)
                    data = {"kind": "item", "runs": runs, "work_item_id": runs[0].get("work_item_id")}
                    rows.append((ikey, pkey, activity.work_item_label(runs[0]), data))
                    for entry in runs:
                        rows.append(self._entry_row(prefix, ikey, entry, now))
            if grouped.sessions:
                skey: Key = (prefix, "sessions")
                rows.append(
                    (skey, top, Text("your sessions and conversations", style="bold"), {"kind": "section"})
                )
                for entry in grouped.sessions:
                    rows.append(self._entry_row(prefix, skey, entry, now))

        section("live", list(self.board.in_flight.values()), None)
        if self.board.finished:
            section("ended", list(self.board.finished.values()), Text("Ended in the last hour", style="bold"))
        rows += self._step_rows(rows)
        return [row[0] for row in rows], rows

    def _entry_row(
        self, prefix: str, parent: Key, entry: dict[str, Any], now: datetime
    ) -> tuple[Key, Key | None, Text, dict[str, Any]]:
        key: Key = (prefix, str(entry.get("kind")), str(entry.get("id")))
        return (
            key,
            parent,
            activity.line(entry, now, self.out, compact=True),
            {"kind": str(entry.get("kind")), "entry": entry},
        )

    def _step_rows(
        self, rows: list[tuple[Key, Key | None, Text, dict[str, Any]]]
    ) -> list[tuple[Key, Key | None, Text, dict[str, Any]]]:
        steps: list[tuple[Key, Key | None, Text, dict[str, Any]]] = []
        for key, _parent, _label, data in rows:
            if data.get("kind") == "run":
                for number, text in enumerate(run_steps(data["entry"], self.out)):
                    steps.append(((*key, "step", str(number)), key, text, {"kind": "step"}))
        return steps

    def draw_tree(self) -> None:
        tree = self.query_one(FlightTree)
        shape, rows = self._tree_shape()
        if shape == self._tree_keys:  # same nodes: only the times and the figures moved
            for key, _parent, label, data in rows:
                node = self._by_key[key]
                node.set_label(label)
                node.data = data
            return
        selected = self._key_of(tree.cursor_node) if tree.cursor_node is not None else None
        tree.clear()
        self._by_key = {}
        for key, parent, label, data in rows:
            holder = tree.root if parent is None else self._by_key[parent]
            kind = data.get("kind")
            if kind in ("step",) or (
                kind in ("run", "session", "turn") and not self._has_children(key, rows)
            ):
                node = holder.add_leaf(label, data=data)
            else:
                default = kind in ("project", "item", "section")
                node = holder.add(label, data=data, expand=self._user_open.get(key, default))
            self._by_key[key] = node
        self._tree_keys = shape
        if not rows:
            tree.root.add_leaf(Text("nothing is running"), data={"kind": "empty"})
        elif selected in self._by_key:
            tree.move_cursor(self._by_key[selected], animate=False)
        tree.border_subtitle = f"{self.board.running()} running" if self.live else "reconnecting"

    @staticmethod
    def _has_children(key: Key, rows: list[tuple[Key, Key | None, Text, dict[str, Any]]]) -> bool:
        return any(parent == key for _k, parent, _l, _d in rows)

    def _key_of(self, node: TreeNode[dict[str, Any]]) -> Key | None:
        return next((key for key, found in self._by_key.items() if found is node), None)

    @on(Tree.NodeExpanded)
    @on(Tree.NodeCollapsed)
    def remember(self, event: Tree.NodeExpanded[dict[str, Any]] | Tree.NodeCollapsed[dict[str, Any]]) -> None:
        key = self._key_of(event.node)
        if key is not None:
            self._user_open[key] = event.node.is_expanded

    def draw_decisions(self) -> None:
        body = self.query_one("#decisions-body", Static)
        lines: list[RenderableType] = []
        for action in self.proposals:
            head = theme.badge(theme.WAITING, self.out)
            head.append(f"  {action.get('summary') or action.get('operation')}")
            lines.append(head)
            lines.append(
                Text(f"  proposed in this conversation · confirm in the console: {self._confirm_url(action)}")
            )
        for action in self.pending or []:
            head = theme.badge(theme.WAITING, self.out)
            head.append(f"  {action.get('title')}")
            lines.append(head)
            note = f"  {action.get('kind')} · governed action awaiting approval"
            lines.append(Text(note))
        if not lines:
            lines.append(
                Text(
                    "nothing waits for you in what this view reads"
                    if self.pending is not None
                    else f"{DASH} the organisation's pending actions are not readable by you"
                )
            )
        lines.append(Text("read-only here: decide in the console", style="bold"))
        body.update(Group(*lines))

    def _confirm_url(self, action: dict[str, Any]) -> str:
        return confirm_link(self.api.profile.api_url, str(self.conversation_id), str(action["id"]))

    def draw_elsewhere(self) -> None:
        inbox: list[Text] = []
        for item in self.inbox[:5]:
            inbox.append(Text(f"{item.get('name')} · {item.get('task')} v{item.get('version')}"))
        self.query_one("#inbox", Static).update(Group(*inbox) if inbox else Text("nothing delivered yet"))
        recent: list[Text] = []
        for summary in self.conversations[:5]:
            label = Text(str(summary.get("title") or "(untitled)"))
            label.append(
                f" · {summary.get('assistant') or summary.get('agent')} · ${summary.get('cost_usd', 0):.2f}"
            )
            recent.append(label)
        self.query_one("#recent", Static).update(Group(*recent) if recent else Text("no conversation yet"))

    # ─────────────────────────── the conversation ───────────────────────────

    def pick_assistant_quietly(self) -> None:
        if self.assistant:
            return
        ready = [a["slug"] for a in self.assistants if a.get("status") == "ready"]
        if len(ready) == 1:
            self.assistant = ready[0]

    def show_welcome(self) -> None:
        pane = self.query_one("#conversation", VerticalScroll)
        pane.remove_children()
        ready = [str(a["slug"]) for a in self.assistants if a.get("status") == "ready"]
        if self.assistant:
            text = Text(f"Talking to {self.assistant}. ")
            text.append("Type below and press Enter.")
        elif ready:
            text = Text(f"Several assistants are ready for you ({', '.join(ready)}): Ctrl+A chooses one.")
        else:
            text = Text(f"{DASH} no assistant is ready for you in {self.organisation}.")
        pane.mount(Static(text, classes="message"))
        self.draw_title()

    def say(self, role: str, text: str) -> Static:
        pane = self.query_one("#conversation", VerticalScroll)
        widget = Static(self._message(role, text), classes="message")
        pane.mount(widget)
        pane.scroll_end(animate=False)
        return widget

    def _message(self, role: str, text: str, *, note: str = "") -> RenderableType:
        if role == "user":
            return Group(Text("you", style="bold"), Text(text))
        name = Text(
            self.assistant or "assistant", style=f"bold {theme.hue_style(self.assistant or 'assistant')}"
        )
        if note:
            name.append(f"  {note}")
        return Group(name, Markdown(text) if text else Text(""))

    @on(Input.Submitted, "#prompt")
    async def submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._turn_running:
            self.notify("a turn is running: Esc cancels it", severity="warning")
            return
        if not self.assistant and not self.conversation_id:
            self.action_assistants()
            self.notify("choose the assistant first", severity="warning")
            return
        event.input.value = ""
        self.run_worker(self.converse(text), group="turn", exit_on_error=False)

    async def converse(self, text: str) -> None:
        """One turn. Cancelling this worker ends the stream, and the platform cancels the turn."""
        self._turn_running = True
        answer = ""
        view: Static | None = None
        note = ""
        try:
            if not self.conversation_id:
                opened = await self.api.post(
                    f"/me/assistants/{self.organisation}/{self.assistant}/conversations", json={}
                )
                self.conversation_id = str(opened["id"])
                self.conversation = opened
            self.say("user", text)
            view = self.say("assistant", "")
            async for event, data in self.api.stream_events(
                "POST",
                f"/me/conversations/{self.conversation_id}/messages",
                json={"input": text, "stream": True},
            ):
                if event == "response.output_text.delta":
                    answer += str(data.get("delta") or "")
                    view.update(self._message("assistant", answer, note="… answering"))
                    self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)
                elif event in ("response.completed", "response.failed"):
                    note = self._ending(data)
                    answer = answer or str(data.get("output_text") or "")
                elif event == "error":
                    note = "✗ " + str(data.get("detail") or data.get("text") or "the turn was refused")
        except asyncio.CancelledError:
            stopped = theme.badge(theme.STOPPED, self.out).plain
            note = f"{stopped}: you cancelled the turn; the platform books what it spent"
            raise
        except (ApiError, httpx.TransportError) as exc:
            note = f"✗ {exc}"
        finally:
            self._turn_running = False
            if view is not None:
                view.update(self._message("assistant", answer, note=note))
            self.draw_title()
        await self.after_turn()

    def _ending(self, data: dict[str, Any]) -> str:
        usage = data.get("usage") or {}
        tokens = f"{int(usage.get('input_tokens') or 0)} in / {int(usage.get('output_tokens') or 0)} out"
        if data.get("status") != "completed":
            error = data.get("error") or {}
            return f"✗ {error.get('message') or 'the turn did not complete'} · {tokens}"
        return f"✓ {tokens}"

    async def after_turn(self) -> None:
        """The turn's writes and the conversation's cost, as `choregos ask` reads them after it."""
        await self.refresh_decisions()
        if self.conversation_id:
            summary = await self._read(f"/me/conversations/{self.conversation_id}")
            if summary:
                self.conversation = summary
                self.query_one(
                    "#conversation"
                ).border_subtitle = f"${float(summary.get('cost_usd') or 0):.4f}"
        self.draw_title()

    def action_cancel_turn(self) -> None:
        """Escape: end the stream (the platform cancels the turn) and keep what was already said."""
        if self._turn_running:
            self.workers.cancel_group(self, "turn")

    async def open_conversation(self, conversation_id: str) -> None:
        summary = await self._read(f"/me/conversations/{conversation_id}")
        if summary is None:
            return
        self.conversation_id, self.conversation = conversation_id, summary
        self.assistant = summary.get("assistant") or self.assistant
        pane = self.query_one("#conversation", VerticalScroll)
        await pane.remove_children()
        for message in summary.get("messages") or []:
            pane.mount(Static(self._message(message["role"], str(message["content"])), classes="message"))
        pane.border_subtitle = f"${float(summary.get('cost_usd') or 0):.4f}"
        pane.scroll_end(animate=False)
        await self.refresh_decisions()
        self.draw_title()

    def action_new_conversation(self) -> None:
        if self._turn_running:
            self.notify("a turn is running: Esc cancels it", severity="warning")
            return
        self.conversation_id, self.conversation, self.proposals = None, None, []
        self.query_one("#conversation").border_subtitle = ""
        self.show_welcome()
        self.draw_decisions()

    def action_conversations(self) -> None:
        options: list[tuple[str, RenderableType]] = []
        for summary in self.conversations:
            label = Text(str(summary.get("title") or "(untitled)"), style="bold")
            who = summary.get("assistant") or summary.get("agent")
            tail = f"  {who} · {summary.get('status')} · ${summary.get('cost_usd', 0):.2f}"
            label.append(tail)
            options.append((str(summary["id"]), label))

        def chosen(conversation_id: str | None) -> None:
            if conversation_id:
                self.run_worker(self.open_conversation(conversation_id), group="load", exit_on_error=False)

        self.push_screen(PickScreen("Your conversations", options, "no conversation yet"), chosen)

    def action_assistants(self) -> None:
        ready = [a for a in self.assistants if a.get("status") == "ready"]
        options: list[tuple[str, RenderableType]] = []
        for found in ready:
            label = Text(str(found["slug"]), style=f"bold {theme.hue_style(str(found['slug']))}")
            label.append(f"  {found.get('display_name') or found.get('name') or ''}")
            options.append((str(found["slug"]), label))

        def chosen(slug: str | None) -> None:
            if slug and slug != self.assistant:
                self.assistant = slug
                if self.conversation and self.conversation.get("assistant") != slug:
                    self.action_new_conversation()
                self.show_welcome() if not self.conversation_id else self.draw_title()

        self.push_screen(
            PickScreen("Assistants ready for you", options, "no assistant is ready for you"), chosen
        )

    # ─────────────────────────────── stopping ───────────────────────────────

    def action_stop_selected(self) -> None:
        tree = self.query_one(FlightTree)
        node = tree.cursor_node
        data = (node.data if node else None) or {}
        kind = data.get("kind")
        if kind == "run":
            entry = data["entry"]
            if not entry.get("can_stop"):
                self.notify("you may not stop this run", severity="warning")
                return
            what = f"run {str(entry['id'])[:12]}"
            if entry.get("work_item_key"):
                what += f" of {entry['work_item_key']}"
            self.ask_reason(what, "POST", f"/runs/{entry['id']}/cancel", {})
        elif kind == "item":
            if not data.get("work_item_id") or not any(r.get("can_stop") for r in data["runs"]):
                self.notify("you may not stop this work item", severity="warning")
                return
            self.ask_reason(
                f"work item {activity.work_item_of(data['runs'][0])}",
                "POST",
                f"/work-items/{data['work_item_id']}/actions",
                {"action": "stop"},
            )
        else:
            self.notify("put the cursor on a run or a work item", severity="information")

    def ask_reason(self, what: str, method: str, path: str, body: dict[str, Any]) -> None:
        def stop(reason: str | None) -> None:
            if reason:
                self.run_worker(
                    self.send_stop(what, method, path, body | {"reason": reason}), exit_on_error=False
                )

        self.push_screen(StopScreen(what), stop)

    async def send_stop(self, what: str, method: str, path: str, body: dict[str, Any]) -> None:
        try:
            await self.api.request(method, path, json=body)
        except (ApiError, httpx.TransportError) as exc:
            self.notify(str(exc), title=f"could not stop {what}", severity="error")
            return
        self.notify(f"{what}: {body['reason']}", title="stop requested")
        with contextlib.suppress(ApiError, httpx.TransportError):
            self.board = activity.Board.of(await self.api.get(f"/orgs/{self.organisation}/activity"))
        self.redraw()
