# SPDX-License-Identifier: Apache-2.0
"""The workspace of `choregos chat` (ADR 0154 §2, §8, §9; ADR 0187 §1), drawn with Textual.

One screen of tabs, from routes that exist on `main` (ADR 0154 §4), under a one-line header
(`org · assistant`, then `N running · N waiting for you · amount today`):

- **1 Conversation**: a durable conversation of ADR 0134, streamed, and a one-line composer `›` with the
  conversation's cost at its right. Ending the turn closes the stream, which cancels it on the platform
  (S63-04b).
- **2 Activity**: the same board and the same lines as `choregos activity` (`activity.Board`,
  `activity.group`, `activity.line`), kept live by the activity stream. `x` stops the run or the work item
  under the cursor, always with a reason (ADR 0142 §2).
- **3 Projects**: every project the person may read (S67-23).
- **4 Decisions**: what waits for the person (`GET /me/pending`: writes and launches an assistant proposed,
  governed actions, human steps). Enter, `d` or F2 opens one: its operation, arguments and diff, then ONE
  explicit key per decision (`a` approve, `r` reject with a reason, `n` answer). Never a batch, never a
  default (ADR 0154 §7; the rules are `decide.py`'s).
- **5 Artefacts**: `/me/inbox`, what was delivered to the person.
- **6 Live**: the live flow of ADR 0187 §4 (S67-28).

`Ctrl-K` opens the palette (S67-24): a fuzzy search by name over the tabs, the projects, the work items, the
assistants, the organisations and the workspace's commands. Nothing is only there: each entry names the key or
the tab that reaches it too.

A **context pane** on the right follows the selection and folds below `CONTEXT_MIN` columns. There is no
frame: one thin rule under the tabs and one before the context pane; titles are words.

The terminal's sixteen colours (`ansi_color`), no background painted, glyph and word before colour; `NO_COLOR`
gives the monochrome rendering. Nothing here runs a tool, a model or an agent on the person's machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar

import httpx
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import on
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Footer, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from . import activity, decide, fuzzy, money, run_activity, theme
from .client import ApiError, AsyncClient, Profile
from .live import _workflow as default_workflow  # the command's own pick: same rule, same words
from .live_app import LiveFlowPane
from .live_view import reason_long
from .run_steps import RunSteps

#: Seconds before the activity stream is opened again after it ended or failed.
RECONNECT_S = 3.0
#: How many conversations the switcher and the "elsewhere" pane list.
CONVERSATIONS_SHOWN = 50
#: How often an expanded run that is still in flight is asked for what is new (its journal, by `after_seq`).
RUN_POLL_S = 2.0
#: Under this many columns the context pane folds away.
CONTEXT_MIN = 120
#: The tabs, in the order of their keys (ADR 0187 §1): `(name, label)`.
TABS = (
    ("conversation", "Conversation"),
    ("activity", "Activity"),
    ("projects", "Projects"),
    ("decisions", "Decisions"),
    ("artefacts", "Artefacts"),
    ("live", "Live"),
)
DASH = activity.DASH
Key = tuple[str, ...]
Row = tuple[Key, Key | None, Text, dict[str, Any]]


def facts(title: Text | str, rows: list[tuple[str, Text | str]], *notes: Text) -> Group:
    """The context pane's shape: a bold title, then aligned `label  value` rows (labels dim), then notes."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(ratio=1)
    for label, value in rows:
        grid.add_row(label, value)
    head = Text(title, style="bold") if isinstance(title, str) else title
    return Group(head, Text(""), grid, *([Text("")] if notes else []), *notes)


def one_line(text: Text) -> Text:
    """A row that never wraps: cut with an ellipsis where the pane ends."""
    text.no_wrap, text.overflow = True, "ellipsis"
    return text


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
    """Start the workspace headless (no terminal), check its parts are there, quit it: what the binary's
    smoke test (`tools/cli_binary/smoke.py`) runs where no person is, to prove Textual and its data files
    are inside the binary."""
    found: list[str] = []

    async def drive(pilot: Any) -> None:
        await pilot.pause()
        for part in ("#header", "#tabs", "#conversation", "#prompt", "#tree", "#decisions"):
            if pilot.app.query(part):
                found.append(part)
        pilot.app.exit()

    Workspace(AsyncClient(profile), organisation).run(headless=True, auto_pilot=drive)
    return f"workspace ok: {len(found)} parts ({' '.join(found)})"


# ──────────────────────────────── what the tree draws ────────────────────────────────


class RunDetail:
    """What an expanded run node shows beyond the activity entry: its journal reduced (S67-03), the latest
    context breakdown and its sub-agent nodes (S67-12). Kept when the node is collapsed, so that opening it
    again asks only for the events after `steps.last_seq`."""

    def __init__(self) -> None:
        self.steps = RunSteps()
        self.breakdown: dict[str, Any] | None = None
        self.agents: list[dict[str, Any]] = []
        self.loaded = False
        #: Why the journal could not be read, in words; `None` when it could.
        self.unreadable: str | None = None


def run_rows(key: Key, detail: RunDetail | None, now: datetime, out: Console) -> list[Row]:
    """The nodes under an expanded run, parents first: its plan, its tool calls, its context and its
    sub-agents. Only what the journal and the platform say is drawn; `_meta` is never read."""
    if detail is None or not (detail.loaded or detail.unreadable):
        return [
            ((*key, "loading"), key, Text("reading the journal…", style="bright_black"), {"kind": "note"})
        ]
    rows: list[Row] = []
    steps = detail.steps
    if detail.unreadable:
        rows.append(((*key, "error"), key, Text(detail.unreadable, style="bright_black"), {"kind": "note"}))
    if steps.plan is not None:
        done = sum(1 for task in steps.plan if task.status == "completed")
        pkey: Key = (*key, "plan")
        rows.append((pkey, key, Text(f"Plan  {done}/{len(steps.plan)} done", style="bold"), {"kind": "plan"}))
        for number, task in enumerate(steps.plan):
            rows.append(((*pkey, str(number)), pkey, run_activity.task_line(task, out), {"kind": "task"}))
    skey: Key = (*key, "steps")
    head = f"Steps  {len(steps.steps)} tool calls · {steps.finished()} finished"
    rows.append((skey, key, Text(head, style="bold"), {"kind": "steps"}))
    shown = list(steps.steps.values())[-run_activity.STEPS_SHOWN :]
    if len(steps.steps) > len(shown):
        earlier = Text(f"… {len(steps.steps) - len(shown)} earlier", style="bright_black")
        rows.append(((*skey, "earlier"), skey, earlier, {"kind": "note"}))
    for step in shown:
        rows.append(((*skey, step.id), skey, run_activity.step_line(step, now, out), {"kind": "step"}))
    if not steps.steps:
        rows.append(((*skey, "none"), skey, Text("no tool call yet", style="bright_black"), {"kind": "note"}))
    if detail.breakdown is None:
        context = Text("Context: not measured", style="bright_black")
    else:
        context = Text(run_activity.context_head(detail.breakdown).replace("Context  ", "Context: ", 1))
    rows.append(((*key, "context"), key, context, {"kind": "context"}))
    for node, parent in activity.sub_agents(detail.agents):
        akey: Key = (*key, "agent", str(node["id"]))
        holder: Key = (*key, "agent", str(parent["id"])) if parent else key
        rows.append((akey, holder, activity.agent_line(node, now, out), {"kind": "agent"}))
    return rows


class FlightTree(Tree[dict[str, Any]]):
    """The live tree: Enter expands a node, `x` stops what is under the cursor."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("x", "stop", "Stop (asks why)")]

    def action_stop(self) -> None:
        app = self.app
        assert isinstance(app, Workspace)
        app.action_stop_selected()


class DecisionList(OptionList):
    """What waits for the person, one line each, grouped by project: Enter or `d` opens the one under the
    cursor (the prompt keeps its letters), F2 anywhere chooses one."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("d", "decide_highlighted", "Decide")]

    def action_decide_highlighted(self) -> None:
        app = self.app
        assert isinstance(app, Workspace)
        app.decide_highlighted()


class DrillList(OptionList):
    """A list of the Projects tab: Enter goes down a level, Backspace or Esc goes back up."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("backspace,escape", "up", "Up", show=False)]

    def action_up(self) -> None:
        app = self.app
        assert isinstance(app, Workspace)
        app.projects_up()


class RunScroll(VerticalScroll):
    """A run's steps in the Projects tab: Backspace or Esc goes back to the item's runs."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("backspace,escape", "up", "Up", show=False)]

    def action_up(self) -> None:
        app = self.app
        assert isinstance(app, Workspace)
        app.projects_up()


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


class TextScreen(ModalScreen[str | None]):
    """One required line (a reason, an answer): empty is not sent, Esc leaves the thing waiting."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "dismiss(None)", "Back")]

    def __init__(self, title: str, placeholder: str, problem: str) -> None:
        super().__init__()
        self.heading, self.placeholder, self.problem = title, placeholder, problem

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading, style="bold"))
            yield Input(placeholder=self.placeholder, id="line", max_length=2000)
            yield Static("", id="problem")
            yield Static("Enter sends the line on to the decision · Esc goes back")

    @on(Input.Submitted, "#line")
    def submit(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            self.query_one("#problem", Static).update(Text(f"✗ {self.problem}", style="red"))
            return
        self.dismiss(line)


class DecisionScreen(ModalScreen[str | None]):
    """One pending thing, shown whole (operation, arguments, diff), then ONE key per decision. No key is the
    default: Enter does nothing here, only `a`, `r` or `n` decide, and Esc leaves it waiting."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss(None)", "Leave it waiting"),
        Binding("a", "choose('approve')", "Approve"),
        Binding("r", "choose('reject')", "Reject"),
        Binding("n", "choose('answer')", "Answer"),
    ]

    def __init__(self, lines: list[Text], verbs: list[str]) -> None:
        super().__init__()
        self.lines, self.verbs = lines, verbs

    def compose(self) -> ComposeResult:
        keys = {"approve": "a approves", "reject": "r rejects", "answer": "n answers"}
        with Vertical(id="decision"):
            yield VerticalScroll(Static(Group(*self.lines)), id="decision-body")
            yield Static(
                " · ".join([keys[v] for v in self.verbs] + ["Esc leaves it waiting"])
                if self.verbs
                else "decide it in the console · Esc closes"
            )

    def action_choose(self, verb: str) -> None:
        if verb in self.verbs:
            self.dismiss(verb)


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


@dataclass(frozen=True)
class Entry:
    """One line of the palette: what it is, what it is called, the key or tab that also reaches it, and what
    choosing it does."""

    kind: str
    label: str
    reach: str
    run: Callable[[], None]


class PaletteScreen(ModalScreen[Entry | None]):
    """`Ctrl-K`: type to narrow every entry by fuzzy match, Up and Down to move, Enter to choose."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("down", "move(1)", "Next", show=False),
        Binding("up", "move(-1)", "Previous", show=False),
    ]
    KIND_WIDTH = 13

    def __init__(self, entries: list[Entry]) -> None:
        super().__init__()
        self.entries = entries
        self.shown: list[Entry] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Input(placeholder="Search by name", id="find")
            yield OptionList(id="found")
            yield Static("Enter chooses · Up and Down move · Esc closes", id="palette-hint")

    def on_mount(self) -> None:
        self.query_one("#find", Input).focus()
        self.narrow("")

    def narrow(self, query: str) -> None:
        listing = self.query_one("#found", OptionList)
        ranked = fuzzy.rank(query, self.entries, lambda e: e.label)
        self.shown = [entry for entry, _positions in ranked]
        listing.clear_options()
        if not ranked:
            listing.add_option(
                Option(Text(f"{DASH} nothing is called like that", style="dim"), disabled=True)
            )
            return
        for n, (entry, positions) in enumerate(ranked):
            line = Text(entry.kind.ljust(self.KIND_WIDTH), style="dim")
            label = Text(entry.label)
            for at in positions:
                label.stylize("bold underline", at, at + 1)
            line.append_text(label)
            line.append(f"   {entry.reach}", style="dim")
            line.no_wrap = True
            line.overflow = "ellipsis"
            listing.add_option(Option(line, id=str(n)))
        listing.highlighted = 0

    @on(Input.Changed, "#find")
    def typed(self, event: Input.Changed) -> None:
        self.narrow(event.value)

    @on(Input.Submitted, "#find")
    def submitted(self, event: Input.Submitted) -> None:
        highlighted = self.query_one("#found", OptionList).highlighted
        if self.shown and highlighted is not None:
            self.dismiss(self.shown[highlighted])

    @on(OptionList.OptionSelected, "#found")
    def chosen(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(self.shown[int(event.option.id)])

    def action_move(self, step: int) -> None:
        listing = self.query_one("#found", OptionList)
        if step > 0:
            listing.action_cursor_down()
        else:
            listing.action_cursor_up()


# ───────────────────────────────────── the app ─────────────────────────────────────


class Workspace(App[None]):
    TITLE = "choregos"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; background: ansi_default; }
    #header { height: 1; padding: 0 2; margin: 1 0 0 0; }
    #org { width: 1fr; }
    #figures { width: auto; }
    #tabs {
        link-style: none; link-color: ansi_default; link-style-hover: none;
        height: 2; padding: 0 1; margin: 1 0 0 0; border-bottom: solid ansi_bright_black;
    }
    #body { height: 1fr; }
    #views { width: 1fr; height: 100%; }
    #context { width: 46; height: 100%; padding: 1 2; border-left: solid ansi_bright_black; }
    .-narrow #context { display: none; }
    #conversation { height: 1fr; padding: 1 2 0 2; scrollbar-size-vertical: 1; }
    .message { margin: 0 0 1 0; }
    #composer { height: 1; margin: 0 2 1 2; }
    #chevron { width: 2; }
    #prompt, #prompt:focus {
        width: 1fr; height: 1; border: none; padding: 0; background: ansi_default; color: ansi_default;
    }
    #prompt > .input--placeholder { color: ansi_bright_black; text-style: none; }
    #cost { width: 10; text-align: right; color: ansi_bright_black; }
    #tree { height: 100%; padding: 1 1 0 1; background: ansi_default; }
    #tree > .tree--guides, #tree > .tree--guides-selected, #tree > .tree--guides-hover {
        color: ansi_bright_black;
    }
    #tree > .tree--cursor { text-style: reverse bold; background: ansi_default; }
    #tree:focus > .tree--cursor { text-style: reverse bold; background: ansi_default; }
    #decisions, #artefacts {
        height: 100%; padding: 1 1 0 1; border: none; background: ansi_default;
    }
    #decisions > .option-list--option-highlighted, #artefacts > .option-list--option-highlighted {
        text-style: reverse bold; background: ansi_default; color: ansi_default;
    }
    #decisions:focus > .option-list--option-highlighted, #artefacts:focus > .option-list--option-highlighted {
        text-style: reverse bold; background: ansi_default; color: ansi_default;
    }
    #decisions > .option-list--option-disabled { color: ansi_default; text-style: bold; }
    #projects { height: 100%; }
    #crumbs { height: 3; padding: 1 2 0 2; color: ansi_bright_black; }
    #projects-list { height: 1fr; padding: 1 1 0 1; border: none; background: ansi_default; }
    #projects-list > .option-list--option-highlighted,
    #projects-list:focus > .option-list--option-highlighted {
        text-style: reverse bold; background: ansi_default; color: ansi_default;
    }
    #projects-list > .option-list--option-disabled { color: ansi_bright_black; }
    #projects-run { height: 1fr; padding: 1 2 0 2; }
    .placeholder { padding: 2 3; color: ansi_bright_black; }
    StopScreen, PickScreen, TextScreen, DecisionScreen, PaletteScreen { align: center middle; }
    #decision { width: 90%; height: 80%; border: round ansi_yellow; padding: 1 2; }
    #decision-body { height: 1fr; }
    #dialog { width: 70%; max-width: 90; height: auto; border: round ansi_default; padding: 1 2; }
    #choices { height: auto; max-height: 14; }
    #palette {
        width: 80%; max-width: 100; height: auto; max-height: 80%; border: round ansi_default; padding: 1 2;
    }
    #find, #find:focus {
        border: none; padding: 0; height: 1; background: ansi_default; color: ansi_default;
    }
    #find > .input--placeholder { color: ansi_bright_black; text-style: none; }
    #found { height: auto; max-height: 14; margin: 1 0; border: none; background: ansi_default; }
    #found > .option-list--option-highlighted, #found:focus > .option-list--option-highlighted {
        text-style: reverse bold; background: ansi_default; color: ansi_default;
    }
    #palette-hint { color: ansi_bright_black; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel_turn", "Cancel the turn"),
        # The screen binds Tab to its own focus ring, nearer than the app: the tabs' keys take it first.
        Binding("tab", "next_view", "Next view", priority=True),
        Binding("shift+tab", "previous_view", "Previous view", show=False, priority=True),
        # The digits are the tabs' keys wherever the person is not typing: the composer keeps its characters.
        *(
            Binding(str(n), f"show_view('{name}')", "View", show=False)
            for n, (name, _label) in enumerate(TABS, start=1)
        ),
        Binding("ctrl+o", "conversations", "Conversations"),
        Binding("ctrl+n", "new_conversation", "New"),
        # The composer binds Ctrl+A to "start of line" (`home` does that): choosing the assistant is first.
        Binding("ctrl+a", "assistants", "Assistant", priority=True),
        Binding("f2", "decide", "Decide"),
        # The composer binds Ctrl+K to "delete to the end of the line": the palette comes first.
        Binding("ctrl+k", "palette", "Palette", priority=True),
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
        self.today = money.Amount()
        #: The organisation's currency and its ledger's ratio (S67-25): every amount goes through `price`.
        self.currency: str | None = None
        self.rate: float | None = None
        self.assistants: list[dict[str, Any]] = []
        #: The organisations the person may switch to (`GET /orgs`), by slug.
        self.organisations: list[str] = []
        self.conversations: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] | None = None
        #: What the last decision said (the API's own words on a refusal): the person's, and the tests'.
        self.outcome = ""
        self._deciding = False
        self.inbox: list[dict[str, Any]] = []
        #: The organisation's projects, `None` while unread.
        self.projects: list[dict[str, Any]] | None = None
        #: How deep the Projects tab is: 0 projects, 1 items, 2 runs, 3 a run's steps.
        self.depth = 0
        self._project_shape: list[tuple[str | None, str]] | None = None
        self.project: dict[str, Any] | None = None
        self.item: dict[str, Any] | None = None
        self.run_summary: dict[str, Any] | None = None
        self.items: list[dict[str, Any]] | None = None
        self.item_runs: list[dict[str, Any]] | None = None
        self.live = False
        #: The tab on show (a name of `TABS`).
        self.current_view = "conversation"
        self._tab_gap = 4
        self._user_open: dict[Key, bool] = {}
        self._tree_keys: list[Key] = []
        self._by_key: dict[Key, TreeNode[dict[str, Any]]] = {}
        self._turn_running = False
        #: The runs whose node the person has expanded, by run id: they are read while they are open.
        self._open_runs: set[str] = set()
        self._details: dict[str, RunDetail] = {}
        #: What the decisions and artefacts lists show now, to redraw only what changed.
        self._decision_shape: list[Any] = []
        self._artefact_shape: list[Any] = []

    # ───────────────────────────── layout ─────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(id="org")
            yield Static(id="figures")
        yield Static(id="tabs")
        with Horizontal(id="body"):
            with ContentSwitcher(initial="view-conversation", id="views"):
                with Vertical(id="view-conversation"):
                    yield VerticalScroll(id="conversation")
                    with Horizontal(id="composer"):
                        yield Static("›", id="chevron")
                        yield Input(placeholder="Say something", id="prompt")
                        yield Static(id="cost")
                yield FlightTree("", id="tree")
                with Vertical(id="projects"):
                    yield Static(id="crumbs")
                    yield DrillList(id="projects-list")
                    with RunScroll(id="projects-run"):
                        yield Static(id="projects-run-body")
                yield DecisionList(id="decisions")
                yield OptionList(id="artefacts")
                with Vertical(id="live-view"):
                    yield Static("", id="live-note", classes="placeholder")
            with VerticalScroll(id="context"):
                yield Static(id="context-body")
        yield Footer()

    #: The id of the widget each tab shows (the content switcher's children are named after them).
    VIEW_WIDGET: ClassVar[dict[str, str]] = {
        "conversation": "view-conversation",
        "activity": "tree",
        "projects": "projects",
        "decisions": "decisions",
        "artefacts": "artefacts",
        "live": "live-view",
    }
    #: What takes the focus when a tab opens.
    VIEW_FOCUS: ClassVar[dict[str, str]] = {
        "conversation": "#prompt",
        "activity": "#tree",
        "projects": "#projects-list",
        "decisions": "#decisions",
        "artefacts": "#artefacts",
        "live": "#live-view",  # the pane takes it once it is mounted (`open_live`)
    }

    def on_resize(self, event: Any) -> None:
        """Below `CONTEXT_MIN` columns the context pane folds away: the main view takes the width."""
        self.screen.set_class(event.size.width < CONTEXT_MIN, "-narrow")
        self._tab_gap = 2 if event.size.width < 90 else 4
        self.draw_tabs()

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        tree = self.query_one(FlightTree)
        tree.show_root = False
        tree.show_guides = True
        self.query_one("#projects-run").display = False
        self.query_one("#prompt", Input).focus()
        self.set_interval(activity.REDRAW_EVERY_S, self.tick)
        self.draw_header()
        self.draw_tabs()
        self.run_worker(self.load(), group="load", exit_on_error=False)
        self.run_worker(self.follow_activity(), group="activity", exit_on_error=False)

    # ───────────────────────────── the tabs ─────────────────────────────

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """The tab keys belong to the workspace, not to a dialog over it."""
        ours = action in ("show_view", "next_view", "previous_view", "assistants", "conversations", "palette")
        return not (ours and isinstance(self.screen, ModalScreen))

    def action_show_view(self, name: str) -> None:
        """Show a tab (`1`..`6`, a click on its name, Tab) and move the focus into it."""
        if name not in self.VIEW_WIDGET:
            return
        leaving_live = self.current_view == "live" and name != "live"
        self.current_view = name
        self.query_one("#views", ContentSwitcher).current = self.VIEW_WIDGET[name]
        self.draw_tabs()
        self.draw_context()
        if leaving_live:
            self.run_worker(self.close_live(), group="live-tab", exit_on_error=False)  # no stream unseen
        if name == "live":
            self.run_worker(self.open_live(), group="live-tab", exit_on_error=False)
        else:
            self.query_one(self.VIEW_FOCUS[name]).focus()
        if name == "projects":
            self.run_worker(self.refresh_projects(), group="projects", exit_on_error=False)

    # ───────────────────────────── the Live tab ─────────────────────────────

    def _live_project(self) -> dict[str, Any] | None:
        """The project the Live tab draws: the one open in Projects, else the one under its cursor, else the
        first."""
        if self.depth >= 1 and self.project is not None:
            return self.project
        listed = self.projects or []
        picked = self._highlighted(self.query_one("#projects-list", DrillList))
        return next((p for p in listed if str(p["id"]) == picked), listed[0] if listed else None)

    def _live_pane(self) -> LiveFlowPane | None:
        found = self.query(LiveFlowPane)
        return found.first() if found else None

    def _say_live(self, text: Text) -> None:
        note = self.query_one("#live-note", Static)
        note.update(text)
        note.display = True

    async def open_live(self) -> None:
        """Draw the live flow of the project's default workflow: `choregos live`'s pane, the same widget."""
        project = self._live_project()
        if project is None:
            pane = self._live_pane()
            if pane is not None:
                await pane.remove()
            self._say_live(
                Text(
                    f"{DASH} the projects are not readable right now"
                    if self.projects is None
                    else "No project to show yet.",
                    style="dim",
                )
            )
            return
        try:
            listed = await self.api.get(f"/projects/{project['slug']}/workflows")
            listed = listed["items"] if isinstance(listed, dict) else listed or []
            name, is_default = default_workflow(listed, None)
        except (ApiError, httpx.TransportError, LookupError) as exc:
            self._say_live(Text(f"{project['name']}: {exc}", style="red"))
            return
        if self.current_view != "live":
            return  # the person went elsewhere while the workflows were read
        pane = self._live_pane()
        if pane is not None and (pane.project["id"], pane.workflow) == (project["id"], name):
            pane.focus()
            return
        if pane is not None:
            await pane.remove()
        self.query_one("#live-note", Static).display = False
        pane = LiveFlowPane(self.api, self.organisation, project, name, is_default=is_default)
        await self.query_one("#live-view", Vertical).mount(pane)
        pane.focus()
        self.draw_context()

    async def close_live(self) -> None:
        """Leaving the tab closes its stream: the pane is read again, from fresh, when the tab is shown."""
        pane = self._live_pane()
        if pane is not None and self.current_view != "live":
            await pane.remove()
            self.draw_context()

    @on(LiveFlowPane.Moved)
    def live_moved(self) -> None:
        self.draw_context()

    def _context_live(self) -> RenderableType:
        pane = self._live_pane()
        if pane is None or pane.view is None:
            return Text(
                "The live flow of the project's workflow, drawn from what its items are doing.", style="dim"
            )
        card = next((c for c in pane.view["cards"] if c["id"] == pane.selected), None)
        if card is None:
            return facts(
                str(pane.project.get("name") or pane.slug),
                [("workflow", pane.workflow)],
                Text("No item is in this workflow yet.", style="dim"),
            )
        lane = next((x["display"] for x in pane.view["lanes"] if x["state"] == card["lane"]), card["lane"])
        rows: list[tuple[str, Text | str]] = [("station", str(lane)), ("workflow", pane.workflow)]
        notes: list[Text] = []
        status = card["status"]
        if status == "failed":
            failure = card.get("failure") or {}
            rows.append(("failed", Text(reason_long(failure.get("reason")), style="red")))
            if failure.get("summary"):
                notes.append(Text(decide.clean(str(failure["summary"]))))
        elif status == "waiting":
            waiting = card.get("waiting") or {}
            rows.append(("waiting for", str(waiting.get("label") or waiting.get("kind") or DASH)))
        elif status == "running":
            running = card.get("running") or {}
            rows.append(
                ("running", " · ".join(str(running[k]) for k in ("stage", "agent") if running.get(k)))
            )
        else:
            rows.append(("status", str(status)))
        notes += [Text(""), Text("Enter opens it.", style="dim")]
        return facts(
            Text.assemble((str(card["key"]), "bold"), "  ", decide.clean(str(card["title"]))), rows, *notes
        )

    def _step_view(self, step: int) -> None:
        names = [name for name, _label in TABS]
        self.action_show_view(names[(names.index(self.current_view) + step) % len(names)])

    def action_next_view(self) -> None:
        self._step_view(1)

    def action_previous_view(self) -> None:
        self._step_view(-1)

    def draw_tabs(self) -> None:
        """`1 Conversation   2 Activity …`: the open one bold and underlined, the others dim; what waits for
        the person is a count in amber beside Decisions. Each name is a click."""
        bar = Text()
        for number, (name, label) in enumerate(TABS, start=1):
            click = Style(meta={"@click": f"app.show_view('{name}')"})
            current = name == self.current_view
            bar.append(f"{number} ", Style(dim=True) + click)
            bar.append(label, (Style(bold=True, underline=True) if current else Style(dim=True)) + click)
            if name == "decisions" and self.pending:
                bar.append(f" {len(self.pending)}", Style(color="yellow", bold=True) + click)
            bar.append(" " * self._tab_gap)
        self.query_one("#tabs", Static).update(bar)

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
        assistants, conversations, inbox, spent, currency, projects = await asyncio.gather(
            self._read("/me/assistants"),
            self._read("/me/conversations", limit=CONVERSATIONS_SHOWN),
            self._read("/me/inbox", limit=20),
            self._spend(),
            self._currency(),
            self._read(f"/orgs/{org}/projects"),
        )
        self.projects = None if projects is None else list(projects.get("items") or [])
        self.assistants = [a for a in (assistants or {}).get("items") or [] if a.get("org") == org]
        self.conversations = [
            c for c in (conversations or {}).get("items") or [] if c.get("org") in (org, None)
        ]
        self.inbox = list((inbox or {}).get("items") or [])
        self.today, self.rate = spent
        self.currency = currency
        if self.conversation_id:
            await self.open_conversation(self.conversation_id)
        else:
            self.pick_assistant_quietly()
            self.show_welcome()
        await self.refresh_decisions()
        self.redraw()

    def price(self, amount: money.Amount, places: int = 2) -> str:
        """The one place the workspace prints an amount: the organisation's currency, `—` when unknown."""
        return amount.text(self.currency, places, self.rate)

    async def _currency(self) -> str | None:
        """The organisation's currency (`GET /orgs`); `None` when unreadable (own symbols then)."""
        try:
            listed = await self.api.get("/orgs")
        except (ApiError, httpx.TransportError):
            return None
        self.organisations = [str(o["slug"]) for o in listed or [] if o.get("slug")]
        for org in listed or []:
            if self.organisation == org.get("slug") or self.organisation in (org.get("aliases") or []):
                value = org.get("currency") or "EUR"  # the setting's default (S29-03)
                return value if value in money.SYMBOLS else None
        return None

    async def _spend(self) -> tuple[money.Amount, float | None]:
        """What was spent today, and the euros-per-dollar ratio the month's report shows."""
        today = datetime.now(UTC).date()
        first = today.replace(day=1).isoformat()
        day = today.isoformat()
        try:
            report = await self.api.get(
                f"/orgs/{self.organisation}/costs", params={"since": day, "until": day}
            )
            month = await self.api.get(
                f"/orgs/{self.organisation}/costs", params={"since": first, "until": day}
            )
        except (ApiError, httpx.TransportError):
            return money.Amount(), None  # a project member may not read it: "—", never 0
        spent = money.amount((report or {}).get("total"))
        total = money.amount((month or {}).get("total"))
        rate = total.eur / total.usd if total.usd and total.eur else None
        return spent, rate

    async def refresh_decisions(self) -> None:
        """What waits for the person, across the organisations and conversations they belong to."""
        try:
            listed = await self.api.get("/me/pending", params={"limit": 200})
            self.pending = list((listed or {}).get("items") or [])
        except (ApiError, httpx.TransportError):
            self.pending = None
        self.draw_decisions()
        self.draw_header()
        self.draw_tabs()

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
        self.draw_context()

    def redraw(self) -> None:
        self.draw_header()
        self.draw_tabs()
        self.draw_tree()
        self.draw_decisions()
        self.draw_artefacts()
        self.draw_projects()
        self.draw_context()

    def draw_header(self) -> None:
        """`acme · finance` at the left, `1 running · 2 waiting for you · $1.24 today` at the right: what runs
        in blue, what waits for the person in amber, the amount quiet."""
        try:
            org, figures = self.query_one("#org", Static), self.query_one("#figures", Static)
        except NoMatches:  # a read that lands while the workspace closes: nothing left to draw on
            return
        left = Text(self.organisation, style="bold")
        if self.assistant:
            left.append(" · ")
            left.append(self.assistant, style=theme.hue_style(self.assistant))
        org.update(left)
        waiting = DASH if self.pending is None else str(len(self.pending))
        right = Text()
        if not self.live:
            right.append_text(theme.badge(theme.QUEUED, self.out))
            right.append(" reconnecting · ", style="dim")
        right.append(f"{self.board.running()} running", style=theme.RUNNING.style)
        right.append(" · ", style="dim")
        right.append(f"{waiting} waiting for you", style="yellow" if self.pending else "")
        right.append(f" · {self.price(self.today)} today", style="dim")
        figures.update(right)

    def draw_cost(self) -> None:
        cost = money.amount(self.conversation)
        shown = (
            self.price(cost, 4) if self.conversation and self.conversation.get("cost_usd") is not None else ""
        )
        self.query_one("#cost", Static).update(shown)

    def _tree_shape(self) -> tuple[list[Key], list[Row]]:
        """The nodes the tree shows, parents first: `(key, parent, label, data)`."""
        now = activity.clock()
        rows: list[Row] = []

        def section(prefix: str, entries: list[dict[str, Any]], title: Text | None) -> None:
            grouped = activity.group(entries)
            top: Key | None = None
            if title is not None:
                top = (prefix,)
                rows.append((top, None, title, {"kind": "section"}))
            for project, items in grouped.projects.items():
                pkey: Key = (prefix, "project", project)
                rows.append(
                    (
                        pkey,
                        top,
                        Text(project, style=f"bold {theme.hue_style(project)}"),
                        {"kind": "project", "name": project, "items": items},
                    )
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

    def _entry_row(self, prefix: str, parent: Key, entry: dict[str, Any], now: datetime) -> Row:
        key: Key = (prefix, str(entry.get("kind")), str(entry.get("id")))
        return (
            key,
            parent,
            activity.line(entry, now, self.out, compact=True),
            {"kind": str(entry.get("kind")), "entry": entry},
        )

    def _step_rows(self, rows: list[Row]) -> list[Row]:
        """What is under each run node: the reading of its journal, parents before their children."""
        now = activity.clock()
        under: list[Row] = []
        for key, _parent, _label, data in rows:
            if data.get("kind") == "run":
                under += run_rows(key, self._details.get(str(data["entry"]["id"])), now, self.out)
        return under

    def draw_tree(self) -> None:
        try:
            tree = self.query_one(FlightTree)
        except NoMatches:  # a read that lands while the workspace closes: nothing left to draw on
            return
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
            if kind in ("step", "task", "note", "context") or (
                kind in ("run", "session", "turn", "agent") and not self._has_children(key, rows)
            ):
                node = holder.add_leaf(label, data=data)
            elif kind == "run":
                node = holder.add(label, data=data, expand=str(data["entry"]["id"]) in self._open_runs)
            else:
                default = kind in ("project", "item", "section", "plan", "steps", "agent")
                node = holder.add(label, data=data, expand=self._user_open.get(key, default))
            self._by_key[key] = node
        self._tree_keys = shape
        if not rows:
            tree.root.add_leaf(Text("nothing is running", style="dim"), data={"kind": "empty"})
        elif selected in self._by_key:
            # A node knows its line only once the tree has laid its nodes out again.
            node = self._by_key[selected]
            tree.call_after_refresh(tree.move_cursor, node, False)
        else:  # nothing was selected: the first line, so the context pane has something to follow
            tree.call_after_refresh(tree.move_cursor_to_line, 0)

    @staticmethod
    def _has_children(key: Key, rows: list[Row]) -> bool:
        return any(parent == key for _k, parent, _l, _d in rows)

    def _key_of(self, node: TreeNode[dict[str, Any]]) -> Key | None:
        return next((key for key, found in self._by_key.items() if found is node), None)

    @on(Tree.NodeExpanded)
    @on(Tree.NodeCollapsed)
    def remember(self, event: Tree.NodeExpanded[dict[str, Any]] | Tree.NodeCollapsed[dict[str, Any]]) -> None:
        key = self._key_of(event.node)
        if key is None:
            return
        data = event.node.data or {}
        if data.get("kind") == "run":
            # Keyed by the run, not by its place: a run that ends moves from the live part to the ended
            # one and stays open.
            run_id = str(data["entry"]["id"])
            (self._open_runs.add if event.node.is_expanded else self._open_runs.discard)(run_id)
            self.sync_run_readers()
        else:
            self._user_open[key] = event.node.is_expanded

    def sync_run_readers(self) -> None:
        """One reader per run that is open and not yet over; collapsing a run stops its reader."""
        wanted = {run_id for run_id in self._open_runs if run_id in self._run_ids()}
        for worker in list(self.workers):
            group = worker.group
            if group.startswith("run:") and group[4:] not in wanted:
                worker.cancel()
        running = {worker.group for worker in self.workers if worker.group.startswith("run:")}
        for run_id in wanted:
            if f"run:{run_id}" not in running:
                self.run_worker(self.follow_run(run_id), group=f"run:{run_id}", exit_on_error=False)

    def _run_ids(self) -> set[str]:
        return {*self.board.in_flight, *self.board.finished}

    async def follow_run(self, run_id: str) -> None:
        """Reads the open run's journal after the last event it has, its context and its sub-agents; again
        every `RUN_POLL_S` while it is in flight, once when it is over."""
        detail = self._details.setdefault(run_id, RunDetail())
        while True:
            await self.read_run(run_id, detail)
            self.draw_tree()
            if run_id not in self.board.in_flight:
                return
            await asyncio.sleep(RUN_POLL_S)

    async def read_run(self, run_id: str, detail: RunDetail) -> None:
        """One round of reads. A route that fails leaves what was read and says so on the node; it never
        takes the workspace down, and never notifies again every round."""
        found: Any = await asyncio.gather(
            self.api.get(f"/runs/{run_id}/events", params={"after_seq": detail.steps.last_seq}),
            self.api.get(f"/runs/{run_id}/context-breakdown"),
            self.api.get(f"/runs/{run_id}/agents"),
            return_exceptions=True,
        )
        events, context, agents = found
        if isinstance(events, BaseException):
            if not isinstance(events, ApiError | httpx.TransportError):
                raise events
            detail.unreadable = "the journal cannot be read" + (
                f" ({events.status_code})" if isinstance(events, ApiError) else ""
            )
        else:
            detail.unreadable = None
            detail.steps.apply(events or [])
            detail.loaded = True
        # No breakdown is "not measured", whatever the reason (a backend the platform cannot see into, no
        # call measured yet, a 404, or no right to read it): the same words as `activity --run`.
        if not isinstance(context, BaseException):
            detail.breakdown = run_activity.latest_of(context or {})
        elif isinstance(context, ApiError) and context.status_code == 404:
            detail.breakdown = None
        if not isinstance(agents, BaseException):
            detail.agents = list((agents or {}).get("items") or [])

    # ───────────────────────────── the lists ─────────────────────────────

    @staticmethod
    def _highlighted(listing: OptionList) -> str | None:
        option = listing.highlighted_option
        return None if option is None else option.id

    @staticmethod
    def _place_of(item: dict[str, Any]) -> str:
        where = item.get("where") or {}
        if where.get("project_slug"):
            return str(where["project_slug"])
        return "your conversations" if where.get("conversation_id") else str(where.get("org") or DASH)

    def _fill(self, listing: OptionList, options: list[Option]) -> None:
        """Replace a list's options and keep the cursor on the same one when it is still there."""
        before = self._highlighted(listing)
        listing.clear_options()
        listing.add_options(options)
        ids = [option.id for option in options]
        if before is not None and before in ids:
            listing.highlighted = ids.index(before)
        else:
            listing.highlighted = next((n for n, option in enumerate(options) if not option.disabled), None)

    @staticmethod
    def _title_line(what: Any) -> str:
        """A decision's title on one line: its first non-empty line; the whole text is in the context pane."""
        lines = [line.strip() for line in decide.clean(str(what or "")).splitlines()]
        return next((line for line in lines if line), DASH)

    def draw_decisions(self) -> None:
        """Every thing that waits for the person, one line each, grouped by project."""
        try:
            listing = self.query_one(DecisionList)
        except NoMatches:
            return
        shape = (
            [None]
            if self.pending is None
            else [(i["id"], i["what"], self._place_of(i)) for i in self.pending]
        )
        if shape == self._decision_shape:
            return
        self._decision_shape = shape
        options: list[Option] = []
        if self.pending is None:
            options.append(
                Option(
                    Text(f"{DASH} what waits for you is not readable right now", style="dim"), disabled=True
                )
            )
        elif not self.pending:
            options.append(Option(Text("nothing waits for you", style="dim"), disabled=True))
        else:
            glyph = theme.glyph(theme.WAITING, self.out)
            placed: dict[str, list[dict[str, Any]]] = {}
            for item in self.pending:
                placed.setdefault(self._place_of(item), []).append(item)
            for place, items in placed.items():
                options.append(
                    Option(Text(decide.clean(place), style=f"bold {theme.hue_style(place)}"), disabled=True)
                )
                for item in items:
                    line = Text()
                    line.append(f" {glyph} ", style=theme.WAITING.style)
                    line.append(self._title_line(item["what"]))
                    options.append(Option(one_line(line), id=str(item["id"])))
        self._fill(listing, options)
        self.draw_context()

    def draw_artefacts(self) -> None:
        """What was delivered to the person (`GET /me/inbox`), newest first."""
        try:
            listing = self.query_one("#artefacts", OptionList)
        except NoMatches:
            return
        shape = [(a.get("id"), a.get("name"), a.get("version")) for a in self.inbox]
        if shape == self._artefact_shape:
            return
        self._artefact_shape = shape
        options: list[Option] = []
        for found in self.inbox:
            line = Text(f" {found.get('name')}", style="bold")
            line.append(f"  {found.get('task')} v{found.get('version')}", style="dim")
            options.append(Option(one_line(line), id=str(found.get("id"))))
        if not options:
            options.append(Option(Text("nothing delivered yet", style="dim"), disabled=True))
        self._fill(listing, options)
        self.draw_context()

    # ───────────────────────────── the Projects tab (S67-23) ─────────────────────────────

    async def refresh_projects(self) -> None:
        """Opening the tab reads the projects again: a project with no activity is listed too."""
        found = await self._read(f"/orgs/{self.organisation}/projects")
        if found is not None:
            self.projects = list(found.get("items") or [])
        self.draw_projects()
        self.draw_context()

    def _running_in(self, slug: str) -> int:
        return sum(1 for e in self.board.in_flight.values() if e.get("project_slug") == slug)

    def _waiting_in(self, slug: str) -> int:
        return sum(1 for i in self.pending or [] if (i.get("where") or {}).get("project_slug") == slug)

    @staticmethod
    def _cell(value: str, width: int, *, style: str = "", right: bool = False) -> Text:
        return Text(value.rjust(width) if right else value.ljust(width), style=style)

    def _crumbs(self) -> Text:
        parts = ["Projects"]
        if self.project:
            parts.append(str(self.project["name"]))
        if self.item and self.depth >= 2:
            parts.append(str(self.item["tracker_key"]))
        if self.run_summary and self.depth >= 3:
            parts.append(f"{self.run_summary['stage_role']} #{self.run_summary.get('attempt', 1)}")
        crumbs = Text()
        for n, part in enumerate(parts):
            crumbs.append(" › " if n else "", style="dim")
            crumbs.append(part, style="bold" if n == len(parts) - 1 else "dim")
        hint = "Enter opens · Backspace goes up" if self.depth < 3 else "Backspace goes up"
        if self.depth == 0:
            hint = "Enter opens a project"
        crumbs.append("\n")
        crumbs.append(hint, style="dim")
        return crumbs

    def _project_options(self) -> list[Option]:
        if self.projects is None:
            return [
                Option(Text(f"{DASH} the projects are not readable right now", style="dim"), disabled=True)
            ]
        if not self.projects:
            return [Option(Text("this organisation has no project yet", style="dim"), disabled=True)]
        width = min(30, max(len(str(p["name"])) for p in self.projects) + 3)
        head = Text()
        head.append(self._cell("project", width) + self._cell("running", 10) + self._cell("waiting", 10))
        head.append("this month".rjust(12))
        options = [Option(head, disabled=True)]
        for project in self.projects:
            running, waiting = self._running_in(project["slug"]), self._waiting_in(project["slug"])
            month = (project.get("stats") or {}).get("cost_month_eur")
            line = Text()
            line.append(
                self._cell(str(project["name"]), width, style=f"bold {theme.hue_style(project['slug'])}")
            )
            line.append(self._cell(str(running), 10, style="blue" if running else "dim"))
            line.append(self._cell(str(waiting), 10, style="yellow" if waiting else "dim"))
            line.append(self._cell(self.price(money.Amount(eur=month)), 12, style="dim", right=True))
            options.append(Option(one_line(line), id=str(project["id"])))
        return options

    def _item_options(self) -> list[Option]:
        if self.items is None:
            return [
                Option(Text(f"{DASH} the work items are not readable right now", style="dim"), disabled=True)
            ]
        if not self.items:
            return [Option(Text("this project has no work item yet", style="dim"), disabled=True)]
        head = Text()
        head.append(self._cell("item", 12) + self._cell("state", 16) + self._cell("spend", 10) + "title")
        options = [Option(Text(head.plain, style="dim"), disabled=True)]
        for item in self.items:
            cost = (item.get("totals") or {}).get("cost_eur")
            line = Text()
            line.append(self._cell(str(item["tracker_key"]), 12, style="bold"))
            line.append(self._cell(str(item.get("state_display") or item["state"]), 16))
            line.append(self._cell(self.price(money.Amount(eur=cost)), 10, style="dim"))
            line.append(decide.clean(str(item["title"])).splitlines()[0] if item["title"] else DASH)
            options.append(Option(one_line(line), id=str(item["id"])))
        return options

    def _run_options(self) -> list[Option]:
        if self.item_runs is None:
            return [Option(Text(f"{DASH} the runs are not readable right now", style="dim"), disabled=True)]
        if not self.item_runs:
            return [Option(Text("this work item has no run yet", style="dim"), disabled=True)]
        head = Text(
            "status".ljust(16) + "stage".ljust(14) + "try".ljust(5) + "spend".ljust(10) + "backend · model",
            style="dim",
        )
        options = [Option(head, disabled=True)]
        for run in self.item_runs:
            status = theme.status_of(run["status"])
            line = theme.badge(status, self.out)
            line.append(" " * max(1, 16 - len(line.plain)))
            line.append(self._cell(str(run["stage_role"]), 14))
            line.append(self._cell(f"#{run.get('attempt', 1)}", 5, style="dim"))
            line.append(self._cell(self.price(money.Amount(usd=run.get("cost_usd"))), 10, style="dim"))
            line.append(
                f"{run.get('backend') or DASH} · {(run.get('model') or DASH).split('/')[-1]}", style="dim"
            )
            options.append(Option(one_line(line), id=str(run["id"])))
        return options

    def draw_projects(self) -> None:
        """The tab's crumbs and its list (or, at the deepest level, the run's steps)."""
        try:
            listing = self.query_one("#projects-list", DrillList)
            self.query_one("#crumbs", Static).update(self._crumbs())
        except NoMatches:
            return
        self.query_one("#projects-run").display = self.depth == 3
        listing.display = self.depth < 3
        if self.depth == 3:
            self.draw_run_steps()
            return
        options = (self._project_options, self._item_options, self._run_options)[self.depth]()
        shape = [(o.id, str(o.prompt)) for o in options]
        if shape != getattr(self, "_project_shape", None):
            self._project_shape = shape
            self._fill(listing, options)
        self.draw_context()

    def draw_run_steps(self) -> None:
        """A run's plan, tool calls, context and sub-agents: what `activity --run` shows, in the tab."""
        run = self.run_summary
        if run is None:
            return
        rows = run_rows((str(run["id"]),), self._details.get(str(run["id"])), activity.clock(), self.out)
        parents = {key: parent for key, parent, _text, _data in rows}
        lines: list[Text] = []
        for key, _parent, text, _data in rows:
            depth, up = 0, parents[key]
            while up is not None and up in parents:
                depth, up = depth + 1, parents[up]
            line = Text("  " * depth)
            line.append_text(text)
            lines.append(line)
        self.query_one("#projects-run-body", Static).update(Group(*lines))
        self.draw_context()

    @on(OptionList.OptionSelected, "#projects-list")
    def project_opened(self, event: OptionList.OptionSelected) -> None:
        chosen = event.option.id
        if chosen is None:
            return
        if self.depth == 0:
            self.project = next((p for p in self.projects or [] if str(p["id"]) == chosen), None)
            if self.project:
                self.depth, self.items = 1, None
                self.run_worker(self.read_items(), group="projects-read", exit_on_error=False)
        elif self.depth == 1:
            self.item = next((i for i in self.items or [] if str(i["id"]) == chosen), None)
            if self.item:
                self.depth, self.item_runs = 2, None
                self.run_worker(self.read_item_runs(), group="projects-read", exit_on_error=False)
        elif self.depth == 2:
            self.run_summary = next((r for r in self.item_runs or [] if str(r["id"]) == chosen), None)
            if self.run_summary:
                self.depth = 3
                self.run_worker(
                    self.follow_project_run(str(self.run_summary["id"])),
                    group="projects-run",
                    exit_on_error=False,
                )
        self._project_shape = None
        self.draw_projects()
        if self.depth == 3:
            self.query_one("#projects-run").focus()

    async def read_items(self) -> None:
        assert self.project is not None
        found = await self._read(f"/projects/{self.project['id']}/work-items")
        self.items = None if found is None else list(found.get("items") or [])
        self._project_shape = None
        self.draw_projects()

    async def read_item_runs(self) -> None:
        assert self.item is not None
        found = await self._read(f"/work-items/{self.item['id']}/runs")
        self.item_runs = None if found is None else list(found)
        self._project_shape = None
        self.draw_projects()

    async def follow_project_run(self, run_id: str) -> None:
        """The run opened in the Projects tab, read as the tree reads an expanded one: once when it is over,
        again every `RUN_POLL_S` while it runs."""
        detail = self._details.setdefault(run_id, RunDetail())
        while True:
            await self.read_run(run_id, detail)
            self.draw_run_steps()
            if str((self.run_summary or {}).get("status")) not in ("running", "queued", "open"):
                return
            await asyncio.sleep(RUN_POLL_S)

    def projects_up(self) -> None:
        """Backspace or Esc: one level up. At the top Esc falls through to the turn's cancel."""
        if self.depth == 0:
            raise SkipAction()
        for worker in list(self.workers):
            if worker.group == "projects-run":
                worker.cancel()
        self.depth -= 1
        if self.depth == 0:
            self.project = self.item = self.run_summary = None
        elif self.depth == 1:
            self.item = self.run_summary = None
        else:
            self.run_summary = None
        self._project_shape = None
        self.draw_projects()
        self.query_one("#projects-list").focus()

    @on(OptionList.OptionHighlighted)
    @on(Tree.NodeHighlighted)
    def selection_moved(self, event: Any) -> None:
        self.draw_context()

    @on(OptionList.OptionSelected, "#decisions")
    def decision_chosen(self, event: OptionList.OptionSelected) -> None:
        self.decide_highlighted()

    # ───────────────────────────── the context pane ─────────────────────────────

    def draw_context(self) -> None:
        """What the selection of the tab on show is, in full: the pane to the right of the main view."""
        try:
            body = self.query_one("#context-body", Static)
            body.update(self._context_of(self.current_view))
        except NoMatches:  # a read that lands while the workspace closes: nothing left to draw on
            return

    def _context_of(self, view: str) -> RenderableType:
        if view == "conversation":
            return self._context_conversation()
        if view == "activity":
            return self._context_activity()
        if view == "decisions":
            item = next(
                (
                    i
                    for i in self.pending or []
                    if str(i["id"]) == self._highlighted(self.query_one(DecisionList))
                ),
                None,
            )
            return self._context_decision(item)
        if view == "projects":
            return self._context_projects()
        if view == "live":
            return self._context_live()
        if view == "artefacts":
            found = next(
                (
                    a
                    for a in self.inbox
                    if str(a.get("id")) == self._highlighted(self.query_one("#artefacts", OptionList))
                ),
                None,
            )
            return self._context_artefact(found)
        return Text("")

    @staticmethod
    def _when(value: Any) -> str:
        return str(value).replace("T", " ").rstrip("Z")[:16] if value else DASH

    def _context_conversation(self) -> RenderableType:
        found = self.conversation
        parts: list[RenderableType] = []
        if found is None:
            parts.append(
                facts(
                    "New conversation",
                    [("assistant", self.assistant or DASH)],
                    Text("It is opened with your first message.", style="dim"),
                )
            )
        else:
            parts.append(
                facts(
                    str(found.get("title") or "(untitled)"),
                    [
                        ("assistant", str(found.get("assistant") or found.get("agent") or DASH)),
                        ("status", str(found.get("status") or DASH)),
                        ("turns", str(found.get("turns") or 0)),
                        ("cost", self.price(money.amount(found), 4)),
                        ("opened", self._when(found.get("created_at"))),
                    ],
                )
            )
            waiting = [
                i
                for i in self.pending or []
                if (i.get("where") or {}).get("conversation_id") == self.conversation_id
            ]
            if waiting:
                lines = [Text("Waiting for you", style="bold yellow")]
                lines += [one_line(Text(f"  {decide.clean(i['what'])}")) for i in waiting]
                lines.append(Text("  4 shows it, d decides it", style="dim"))
                parts += [Text(""), *lines]
        others = [c for c in self.conversations if c.get("id") != self.conversation_id][:5]
        if others:
            parts += [Text(""), Text("Recent", style="bold")]
            for summary in others:
                row = Text(str(summary.get("title") or "(untitled)"))
                row.append(
                    f"  {summary.get('assistant') or summary.get('agent')} · "
                    f"{self.price(money.amount(summary))}",
                    style="dim",
                )
                parts.append(one_line(row))
            parts.append(Text("ctrl+o opens one", style="dim"))
        return Group(*parts)

    def _context_activity(self) -> RenderableType:
        now = activity.clock()
        tree = self.query_one(FlightTree)
        node = tree.cursor_node
        while (
            node is not None
            and "entry" not in (node.data or {})
            and (node.data or {}).get("kind")
            not in (
                "item",
                "project",
            )
        ):
            node = node.parent
        data = (node.data if node else None) or {}
        if "entry" in data:
            return self.entry_facts(data["entry"], now)
        if data.get("kind") == "item":
            runs = data["runs"]
            first = runs[0]
            states = ", ".join(sorted({theme.status_of(str(r.get("status"))).word for r in runs}))
            return facts(
                activity.work_item_label(first),
                [
                    ("project", str(first.get("project_slug") or DASH)),
                    ("workflow", str(first.get("workflow") or DASH)),
                    ("runs", f"{len(runs)} · {states}"),
                ],
            )
        if data.get("kind") == "project":
            return facts(
                str(node.label) if node else "",
                [],
                Text("The project's items are under it.", style="dim"),
            )
        return Text("Move to a run to see it in full.", style="dim")

    def entry_facts(self, entry: dict[str, Any], now: datetime) -> RenderableType:
        status = theme.badge(theme.status_of(str(entry.get("status") or "")), self.out)
        title = Text(
            activity.work_item_of(entry)
            if entry.get("work_item_id") or entry.get("work_item_key")
            else str(entry.get("kind")),
            style="bold",
        )
        if entry.get("project_slug"):
            title.append(f"  {entry['project_slug']}", style=theme.hue_style(str(entry["project_slug"])))
        rows: list[tuple[str, Text | str]] = [
            ("status", Text.assemble(status, f"  {activity.elapsed(entry, now)}")),
            ("stage", str(entry.get("stage_role") or DASH)),
            ("agent", str(entry.get("agent_slug") or DASH)),
            ("workflow", str(entry.get("workflow") or DASH)),
            ("model", activity.model_name(entry.get("model")) or DASH),
            ("by", str(entry.get("initiated_by") or DASH)),
            ("started", self._when(entry.get("started_at"))),
            ("spend", activity.spend(entry.get("spend"))),
            ("steps", str(entry.get("steps") if entry.get("steps") is not None else DASH)),
            ("id", str(entry.get("id"))),
        ]
        notes: list[Text] = []
        failure = entry.get("failure")
        if entry.get("status") == "failed" and failure:
            rows.insert(1, ("reason", Text(reason_long(failure.get("reason")), style="red")))
            if failure.get("summary"):
                notes.append(Text(decide.clean(str(failure["summary"]))))
        stop = (
            Text("x stops it, with a reason", style="dim")
            if entry.get("can_stop") and entry.get("status") in activity.IN_FLIGHT
            else Text("")
        )
        return facts(title, rows, *notes, stop)

    def _context_projects(self) -> RenderableType:
        picked = self._highlighted(self.query_one("#projects-list", DrillList)) if self.depth < 3 else None
        if self.depth == 0:
            project = next((p for p in self.projects or [] if str(p["id"]) == picked), None)
            if project is None:
                return Text("Move to a project to see it in full.", style="dim")
            stats = project.get("stats") or {}
            slug = project["slug"]
            budget = stats.get("budget_month_eur")
            return facts(
                Text(str(project["name"]), style=f"bold {theme.hue_style(slug)}"),
                [
                    ("status", str(project["status"])),
                    ("running", str(self._running_in(slug))),
                    ("waiting", str(self._waiting_in(slug))),
                    ("work items", str(stats.get("active_work_items", DASH))),
                    ("this month", self.price(money.Amount(eur=stats.get("cost_month_eur")))),
                    ("budget", self.price(money.Amount(eur=budget))),
                    ("workflow", str(project.get("workflow_name") or DASH)),
                ],
            )
        if self.depth == 1:
            item = next((i for i in self.items or [] if str(i["id"]) == picked), None)
            if item is None:
                return Text("Move to a work item to see it in full.", style="dim")
            return self._item_facts(item)
        run = (
            self.run_summary
            if self.depth == 3
            else next((r for r in self.item_runs or [] if str(r["id"]) == picked), None)
        )
        if run is None:
            return Text("Move to a run to see it in full.", style="dim")
        status = theme.status_of(run["status"])
        return facts(
            theme.badge(status, self.out),
            [
                ("run", str(run["id"])),
                ("stage", str(run["stage_role"])),
                ("try", str(run.get("attempt", 1))),
                ("backend", str(run.get("backend") or DASH)),
                ("model", str(run.get("model") or DASH)),
                ("spend", self.price(money.Amount(usd=run.get("cost_usd")))),
                ("started", self._when(run.get("started_at"))),
            ],
        )

    def _item_facts(self, item: dict[str, Any]) -> RenderableType:
        totals = item.get("totals") or {}
        return facts(
            Text(str(item["tracker_key"]), style="bold"),
            [
                ("state", str(item.get("state_display") or item["state"])),
                ("size", str(item.get("size") or DASH)),
                ("spend", self.price(money.Amount(eur=totals.get("cost_eur")))),
                ("pull request", str(item.get("pr_url") or DASH)),
            ],
            Text(""),
            Text(decide.clean(str(item["title"]))),
        )

    def _context_decision(self, item: dict[str, Any] | None) -> RenderableType:
        if item is None:
            return Text(
                "Nothing waits for you."
                if self.pending is not None
                else "What waits for you is not readable.",
                style="dim",
            )
        where = item.get("where") or {}
        place = " · ".join(
            decide.clean(where[k]) for k in ("org", "project_slug", "work_item_key") if where.get(k)
        )
        return facts(
            Text(decide.clean(decide.KIND_LABEL.get(item["kind"], item["kind"])), style="bold yellow"),
            [("where", place or DASH), ("since", self._when(item.get("since")))],
            Text(decide.clean(item["what"])),
            Text(""),
            Text("Enter or d shows it whole, then one key decides.", style="dim"),
        )

    def _context_artefact(self, found: dict[str, Any] | None) -> RenderableType:
        if found is None:
            return Text("Nothing was delivered to you yet.", style="dim")
        return facts(
            str(found.get("name")),
            [
                ("task", str(found.get("task") or DASH)),
                ("version", f"v{found.get('version')}"),
                ("type", str(found.get("type") or DASH)),
                ("media", str(found.get("media_type") or DASH)),
                ("delivered", self._when(found.get("created_at"))),
            ],
        )

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
        text.stylize("dim")
        pane.mount(Static(text, classes="message"))
        self.draw_header()
        self.draw_cost()
        self.draw_context()

    def say(self, role: str, text: str) -> Static:
        pane = self.query_one("#conversation", VerticalScroll)
        widget = Static(self._message(role, text), classes="message")
        pane.mount(widget)
        pane.scroll_end(animate=False)
        return widget

    def _message(self, role: str, text: str, *, note: str = "") -> RenderableType:
        """`you   …` and `finance   …`: the speaker in a quiet column, what was said beside it."""
        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(width=12, no_wrap=True, overflow="ellipsis")
        grid.add_column(ratio=1)
        if role == "user":
            grid.add_row(Text("you", style="dim"), Text(text))
            return grid
        who = self.assistant or "assistant"
        body: list[RenderableType] = [Markdown(text)] if text else []
        if note:
            body.append(Text(note, style="red" if note.startswith("✗") else "dim"))
        grid.add_row(Text(who, style=f"bold {theme.hue_style(who)}"), Group(*body))
        return grid

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
            self.draw_header()
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
        self.draw_header()
        self.draw_cost()
        self.draw_context()

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
        pane.scroll_end(animate=False)
        await self.refresh_decisions()
        self.draw_header()
        self.draw_cost()
        self.draw_context()

    def action_new_conversation(self) -> None:
        if self._turn_running:
            self.notify("a turn is running: Esc cancels it", severity="warning")
            return
        self.conversation_id, self.conversation = None, None
        self.show_welcome()
        self.draw_decisions()
        self.action_show_view("conversation")

    def action_conversations(self) -> None:
        options: list[tuple[str, RenderableType]] = []
        for summary in self.conversations:
            label = Text(str(summary.get("title") or "(untitled)"), style="bold")
            who = summary.get("assistant") or summary.get("agent")
            tail = f"  {who} · {summary.get('status')} · {self.price(money.amount(summary))}"
            label.append(tail)
            options.append((str(summary["id"]), label))

        def chosen(conversation_id: str | None) -> None:
            if conversation_id:
                self.action_show_view("conversation")
                self.run_worker(self.open_conversation(conversation_id), group="load", exit_on_error=False)

        self.push_screen(PickScreen("Your conversations", options, "no conversation yet"), chosen)

    def action_assistants(self) -> None:
        ready = [a for a in self.assistants if a.get("status") == "ready"]
        options: list[tuple[str, RenderableType]] = []
        for found in ready:
            label = Text(str(found["slug"]), style=f"bold {theme.hue_style(str(found['slug']))}")
            label.append(f"  {found.get('display_name') or found.get('name') or ''}")
            options.append((str(found["slug"]), label))

        self.push_screen(
            PickScreen("Assistants ready for you", options, "no assistant is ready for you"),
            self.choose_assistant,
        )

    def choose_assistant(self, slug: str | None) -> None:
        if slug and slug != self.assistant:
            self.assistant = slug
            if self.conversation and self.conversation.get("assistant") != slug:
                self.action_new_conversation()
            self.show_welcome() if not self.conversation_id else self.draw_header()

    # ───────────────────────────── the palette (S67-24) ─────────────────────────────

    def action_palette(self) -> None:
        self.push_screen(PaletteScreen(self.palette_entries()), self._palette_chose)

    @staticmethod
    def _palette_chose(entry: Entry | None) -> None:
        if entry is not None:
            entry.run()

    def palette_entries(self) -> list[Entry]:
        """Everything the person can reach by name, each with the key or tab that reaches it too."""
        entries = [
            Entry("tab", f"{label}", f"key {n}", partial(self.action_show_view, name))
            for n, (name, label) in enumerate(TABS, start=1)
        ]
        entries += [
            Entry("command", "Decide what waits for me", "F2", self.action_decide),
            Entry("command", "Stop the selected run or item", "x", self._stop_from_palette),
            Entry("command", "New conversation", "Ctrl-N", self.action_new_conversation),
            Entry("command", "Open a conversation", "Ctrl-O", self.action_conversations),
            Entry("command", "Choose the assistant", "Ctrl-A", self.action_assistants),
            Entry("command", "Cancel the turn", "Esc", self.action_cancel_turn),
            Entry("command", "Quit", "Ctrl-Q", self.action_quit_workspace),
        ]
        for project in self.projects or []:
            entries.append(
                Entry(
                    "project",
                    str(project["name"]),
                    "tab 3",
                    partial(self.open_in_projects, project, None),
                )
            )
        seen: set[str] = set()
        for found in self._known_items():
            if found["id"] in seen:
                continue
            seen.add(found["id"])
            entries.append(
                Entry(
                    "item",
                    found["label"],
                    "tab 3",
                    partial(self.open_in_projects, found["project"], found["id"]),
                )
            )
        for found in self.assistants:
            if found.get("status") == "ready":
                name = str(found.get("display_name") or found.get("name") or "")
                slug = str(found["slug"])
                entries.append(
                    Entry(
                        "assistant",
                        f"{slug} {name}".strip(),
                        "Ctrl-A",
                        partial(self.choose_assistant, slug),
                    )
                )
        for slug in self.organisations:
            if slug != self.organisation:
                entries.append(Entry("organisation", slug, "--org", partial(self.switch_organisation, slug)))
        return entries

    def _known_items(self) -> list[dict[str, Any]]:
        """The work items seen: those of the open project and those on the activity board."""
        by_slug = {str(p["slug"]): p for p in self.projects or []}
        found: list[dict[str, Any]] = []
        for item in self.items or []:
            if self.project:
                title = decide.clean(str(item["title"])).splitlines()[0] if item.get("title") else ""
                label = f"{item['tracker_key']} {title}".strip()
                found.append({"id": str(item["id"]), "label": label, "project": self.project})
        for entry in [*self.board.in_flight.values(), *self.board.finished.values()]:
            project = by_slug.get(str(entry.get("project_slug")))
            if project and entry.get("work_item_id"):
                found.append(
                    {
                        "id": str(entry["work_item_id"]),
                        "label": str(entry.get("work_item_key") or entry["work_item_id"]),
                        "project": project,
                    }
                )
        return found

    def open_in_projects(self, project: dict[str, Any], item_id: str | None) -> None:
        """Show a project (and, given one, a work item's runs) in the Projects tab, from anywhere."""
        for worker in list(self.workers):
            if worker.group in ("projects-run", "projects-read"):
                worker.cancel()
        self.project, self.item, self.run_summary = project, None, None
        self.depth, self.items, self.item_runs = 1, None, None
        self._project_shape = None
        self.action_show_view("projects")
        self.run_worker(self.read_then_open(item_id), group="projects-read", exit_on_error=False)

    async def read_then_open(self, item_id: str | None) -> None:
        await self.read_items()
        if item_id is None:
            return
        self.item = next((i for i in self.items or [] if str(i["id"]) == item_id), None)
        if self.item is None:
            self.notify("that work item is not in this project's list", severity="warning")
            return
        self.depth, self.item_runs = 2, None
        await self.read_item_runs()

    def _stop_from_palette(self) -> None:
        self.action_show_view("activity")
        self.action_stop_selected()

    def action_quit_workspace(self) -> None:
        self.exit()

    def switch_organisation(self, slug: str) -> None:
        """Another organisation: everything read for the first is dropped and read again for the second."""
        if self._turn_running:
            self.notify("a turn is running: Esc cancels it", severity="warning")
            return
        for worker in list(self.workers):
            worker.cancel()
        self.organisation, self.assistant = slug, None
        self.conversation_id, self.conversation = None, None
        self.board, self.live = activity.Board(), False
        self.today, self.currency, self.rate = money.Amount(), None, None
        self.assistants, self.conversations, self.inbox, self.projects = [], [], [], None
        self.depth, self.project, self.item, self.run_summary = 0, None, None, None
        self.items = self.item_runs = None
        self._project_shape = None
        self._open_runs, self._details = set(), {}
        self.action_show_view("conversation")
        self.show_welcome()
        self.redraw()
        self.run_worker(self.load(), group="load", exit_on_error=False)
        self.run_worker(self.follow_activity(), group="activity", exit_on_error=False)

    # ───────────────────────────── deciding (ADR 0154 §7) ─────────────────────────────

    def decide_highlighted(self) -> None:
        """Enter or `d` in the decisions tab: the one under the cursor, shown whole before any key decides."""
        item_id = self._highlighted(self.query_one(DecisionList))
        item = next((i for i in self.pending or [] if str(i["id"]) == item_id), None)
        if item is None:
            self.notify("nothing waits for you", severity="information")
            return
        self.run_worker(self.show_decision(item), exit_on_error=False)

    def action_decide(self) -> None:
        """F2: choose ONE of what waits. Nothing is chosen for the person, however short the list."""
        if not self.pending:
            self.notify("nothing waits for you", severity="information")
            return
        options: list[tuple[str, RenderableType]] = []
        for item in self.pending:
            label = Text(decide.clean(item["what"]).splitlines()[0], style="bold")
            label.append(f"  {decide.KIND_LABEL.get(item['kind'], item['kind'])}", style="dim")
            options.append((item["id"], label))

        def chosen(item_id: str | None) -> None:
            item = next((i for i in self.pending or [] if i["id"] == item_id), None)
            if item is not None:
                self.run_worker(self.show_decision(item), exit_on_error=False)

        self.push_screen(PickScreen("Decide one", options, "nothing waits for you"), chosen)

    async def show_decision(self, item: dict[str, Any]) -> None:
        try:
            detail = decide.pick_detail(item, await self.api.get(decide.detail_path(item)))
        except (ApiError, httpx.TransportError) as exc:
            self.notify(str(exc), title="could not read it", severity="error")
            return
        lines = decide.describe(item, detail)
        verbs = decide.verbs(item, detail)

        def verb_chosen(verb: str | None) -> None:
            if verb:
                self.gather(item, verb)

        self.push_screen(DecisionScreen(lines, verbs), verb_chosen)

    def gather(self, item: dict[str, Any], verb: str) -> None:
        """What the verb needs first: an answer, or the reason of a rejection."""
        if verb == "answer":
            self.push_screen(
                TextScreen("Your answer", "answer (required)", "an answer is required: nothing was sent"),
                lambda text: self.send(item, verb, answer=text) if text else None,
            )
        elif decide.asks_reason(item, verb):
            self.push_screen(
                TextScreen(
                    "Why reject it", "reason (required)", "a rejection needs a reason: nothing was sent"
                ),
                lambda text: self.send(item, verb, reason=text) if text else None,
            )
        else:
            self.send(item, verb)

    def send(self, item: dict[str, Any], verb: str, *, reason: str = "", answer: str = "") -> None:
        if self._deciding:
            return  # one decision at a time
        self._deciding = True
        self.run_worker(self.send_decision(item, verb, reason, answer), exit_on_error=False)

    async def send_decision(self, item: dict[str, Any], verb: str, reason: str, answer: str) -> None:
        path, body = decide.request_for(item, verb, reason=reason, answer=answer)
        try:
            await self.api.post(path, **({} if body is None else {"json": body}))
        except ApiError as exc:
            self.outcome = "\n".join(decide.explain(exc))
            self.notify(self.outcome, title=f"{verb} refused", severity="error", timeout=20)
        except httpx.TransportError as exc:
            self.outcome = str(exc) or type(exc).__name__
            self.notify(self.outcome, title=f"{verb} not sent", severity="error", timeout=20)
        else:
            self.outcome = f"{verb} sent: {decide.clean(item['what'])}"
            self.notify(f"✓ {self.outcome}")
            await self.refresh_decisions()
        finally:
            self._deciding = False

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
