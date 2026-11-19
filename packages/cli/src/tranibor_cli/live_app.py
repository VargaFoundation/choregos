# SPDX-License-Identifier: Apache-2.0
"""`choregos live`'s screen (S67-28, ADR 0187 §4): the Textual app. This is the only module of the command
that imports Textual; `live.py` imports it when, and only when, the person opens the screen.

It reads what the console's flow reads, through the same pure reduction (`live_flow`): the workflow's
validated graph, the project's work items, the organisation's activity stream (a run starting or ending moves
a token's status), and a fresh read of the items when a run ends (a token that changed station). `live_view`
draws it. Nothing is written: the screen has no key that acts on a work item (use `choregos decide`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from . import activity, live_flow, live_view, money, run_activity, theme
from .client import ApiError, AsyncClient
from .run_steps import RunSteps

Json = dict[str, Any]

#: Seconds before the activity stream is opened again after it ended or failed, and between two reads of the
#: items while it is down.
RECONNECT_S = 3.0
POLL_S = 5.0
#: A run ending is followed by a fresh read of the items, after this pause (several endings share one read).
RELOAD_DELAY_S = 0.4
TICK_S = 0.1
#: A token takes this long along its arrow; it stays highlighted at its station, and its arrow lit, a while.
TRAVEL_S = 0.6
HIGHLIGHT_S = 4.0
LIT_S = 1.5
TICKER_ROWS = 5
PAGE_SIZE = 200
MAX_PAGES = 25


def mono() -> float:
    """The clock motion is measured on (the tests pin it)."""
    return time.monotonic()


def now() -> datetime:
    """Now, in UTC: what the elapsed times and the day's start are measured against (the tests pin it)."""
    return activity.clock()


def reduced_motion() -> bool:
    return theme.MONOCHROME or "NO_COLOR" in os.environ or bool(os.environ.get("CHOREGOS_NO_MOTION"))


def day_start(moment: datetime) -> str:
    """Midnight, local time, as the UTC instant the reduction counts `today` from."""
    local = moment.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC).isoformat()


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


class ItemScreen(ModalScreen[None]):
    """One work item: what it is, why it waits or failed, and its latest run's steps (`activity --run`)."""

    DEFAULT_CSS = """
    ItemScreen { align: center middle; }
    #item { width: 90%; height: 85%; border: round ansi_default; padding: 1 2; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    def __init__(self, api: AsyncClient, card: Json, lane_display: str, title_style: str) -> None:
        super().__init__()
        self.api, self.card, self.lane_display = api, card, lane_display
        self.title_style = title_style

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(self.head(), id="item-head"), Static("", id="item-run"), id="item")

    def head(self) -> Text:
        card = self.card
        text = Text()
        text.append(str(card["key"]), style="bold")
        text.append("  ")
        text.append(str(card["title"]))
        text.append(f"\n{self.lane_display}", style="dim")
        status = card["status"]
        if status == "failed":
            failure = card["failure"] or {}
            text.append("\n\n")
            text.append(f"failed: {live_view.reason_long(failure.get('reason'))}", style="bold red")
            if failure.get("summary"):
                text.append(f"\n{failure['summary']}")
        elif status == "waiting":
            waiting = card["waiting"] or {}
            text.append("\n\n")
            text.append(
                f"waiting for a person: {waiting.get('label') or waiting.get('kind')}", style="bold yellow"
            )
        elif status == "running":
            running = card["running"] or {}
            text.append("\n\n")
            text.append(
                f"running {running.get('stage') or ''} · {running.get('agent') or ''}".rstrip(" ·"),
                style="bold blue",
            )
        return text

    async def on_mount(self) -> None:
        self.run_worker(self.load(), exclusive=True)

    async def load(self) -> None:
        body = self.query_one("#item-run", Static)
        try:
            runs = await self.api.get(f"/work-items/{self.card['id']}/runs")
            runs = runs["items"] if isinstance(runs, dict) else runs or []
            if not runs:
                body.update(Text("\nno run yet", style="dim"))
                return
            lines = Text("\n")
            for run in runs[:8]:
                lines.append(f"{str(run.get('id'))[:12]}  {run.get('status')}  {run.get('stage') or ''}\n")
            run = runs[0]
            steps = RunSteps()
            steps.apply(await self.api.get(f"/runs/{run['id']}/events", params={"after_seq": 0}) or [])
            breakdown = None
            try:
                breakdown = run_activity.latest_of(
                    await self.api.get(f"/runs/{run['id']}/context-breakdown") or {}
                )
            except ApiError as exc:
                if exc.status_code != 404:
                    raise
            from rich.console import Group

            body.update(Group(lines, run_activity.render(run, steps, breakdown, Console())))
        except ApiError as exc:
            body.update(Text(f"\nthe run could not be read: {exc}", style="red"))


class LiveFlowPane(Vertical, can_focus=True):
    """The live flow of one workflow as a widget (S67-28): `choregos live` is this pane alone, the workspace's
    Live tab is this pane beside its context pane. It owns the reads, the stream and the drawing; the
    reduction (`live_flow`) and the drawing (`live_view`) are shared, nothing is drawn or reduced here."""

    DEFAULT_CSS = """
    LiveFlowPane { layout: vertical; height: 100%; }
    LiveFlowPane #flow-header, LiveFlowPane #flow-summary { height: 1; }
    LiveFlowPane #flow-scroll { height: 1fr; overflow-x: auto; overflow-y: auto; padding: 1 0 0 0; }
    LiveFlowPane #flow { width: auto; height: auto; }
    LiveFlowPane #flow-ticker { height: auto; border-top: solid ansi_bright_black; }
    LiveFlowPane #flow-hint { height: 1; color: ansi_bright_black; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left,up", "step(-1)", "Previous", show=False),
        Binding("right,down,tab", "step(1)", "Next", show=False),
        Binding("enter", "open_item", "Open"),
    ]

    class Moved(Message):
        """The selected token changed (or the first one was chosen): a context pane may follow it."""

    def __init__(
        self,
        api: AsyncClient,
        organisation: str,
        project: Json,
        workflow: str,
        *,
        is_default: bool = False,
        motion: bool = True,
        hint: str = " ←→ token   enter open",
    ) -> None:
        super().__init__(id="flow-pane")
        self.hint = hint
        self.api, self.organisation, self.project = api, organisation, project
        self.workflow, self.is_default = workflow, is_default
        self.motion = motion and not reduced_motion()
        self.ascii = not theme.unicode_ok(Console())
        self.graph: Json | None = None
        self.initial: str | None = None
        self.state: Json | None = None
        self.view: Json | None = None
        self.drawing: live_view.Drawing | None = None
        self.currency: str | None = None  # unknown: each amount in its own symbol, never relabelled
        self.spend: str | None = None
        self.connection = "reconnecting"
        self.error: str | None = None
        self.selected: str | None = None
        #: card id -> (edge id, started on the motion clock): a token that moved.
        self.moves: dict[str, tuple[str | None, float]] = {}
        self._signature: object = None
        self._reload: asyncio.Event | None = None
        self.loaded = False

    # ───────────────────────────────────── layout ─────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static("", id="flow-header")
        yield Static("", id="flow-summary")
        with VerticalScroll(id="flow-scroll"):
            yield Static(Text("loading…", style="dim"), id="flow")
        yield Static("", id="flow-ticker")
        yield Static(Text(self.hint, style="dim"), id="flow-hint")

    async def on_mount(self) -> None:
        self._reload = asyncio.Event()
        self.set_interval(TICK_S, self.tick)
        self.run_worker(self.follow(), exclusive=False, group="follow")
        self.run_worker(self.reload_loop(), exclusive=False, group="reload")

    # ───────────────────────────────────── data ─────────────────────────────────────

    @property
    def slug(self) -> str:
        return str(self.project.get("slug") or self.project["id"])

    async def read_items(self) -> list[Json]:
        found: list[Json] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            page = await self.api.get(f"/projects/{self.slug}/work-items", params=params)
            found += page["items"]
            meta = page.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not meta.get("has_more") or not cursor:
                break
        return [
            item
            for item in found
            if item.get("workflow_name") == self.workflow
            or (item.get("workflow_name") is None and self.is_default)
        ]

    async def read_spend(self) -> None:
        day = now().astimezone().date().isoformat()
        try:
            report = await self.api.get(
                f"/projects/{self.slug}/costs", params={"since": day, "until": day, "workflow": self.workflow}
            )
        except ApiError:
            self.spend = None  # unreadable is a dash, never 0 (ADR 0141 §2)
            return
        total = report.get("total") or {}
        self.spend = money.Amount(usd=total.get("cost_usd"), eur=total.get("cost_eur")).text(self.currency)

    async def load(self) -> None:
        """The first read: the workflow's graph, the organisation's currency, the items, the day's spend."""
        definition = await self.api.get(f"/projects/{self.slug}/workflows/{self.workflow}")
        report = await self.api.post("/workflows/validate", json={"yaml": definition["yaml"]})
        self.graph = report.get("graph")
        self.initial = (definition.get("json") or {}).get("initial")
        try:
            orgs = await self.api.get("/orgs")
            orgs = orgs["items"] if isinstance(orgs, dict) else orgs
            self.currency = next(
                (o.get("currency") or "EUR" for o in orgs if o["slug"] == self.organisation), None
            )
        except ApiError:
            self.currency = None
        items = await self.read_items()
        await self.read_spend()
        self.apply(live_flow.initial_state(self.graph, items, {"day_start": day_start(now())}), reset=True)
        self.loaded = True
        self.error = None

    async def follow(self) -> None:
        """Load, then follow the organisation's activity stream; when it drops, say so and reconnect."""
        while True:
            try:
                if not self.loaded:
                    await self.load()
                await self.listen()
            except ApiError as exc:
                self.error = str(exc)
            except Exception as exc:  # a dropped connection is a state to show, not a crash
                self.error = f"{type(exc).__name__}: {exc}"
            self.connection = "reconnecting"
            await asyncio.sleep(RECONNECT_S)
            if self.loaded:
                with contextlib.suppress(Exception):
                    await self.refresh_items()

    async def listen(self) -> None:
        async for event, entry in self.api.stream_events(
            "GET", f"/orgs/{self.organisation}/activity/stream", params={"project": self.slug}
        ):
            self.connection = "live"
            self.error = None
            if self.state is None or event not in ("activity.upsert", "activity.ended"):
                continue
            self.apply(live_flow.apply_event(self.state, event, entry))
            if event == "activity.ended" and self._reload is not None:
                self._reload.set()

    async def reload_loop(self) -> None:
        assert self._reload is not None
        while True:
            await self._reload.wait()
            await asyncio.sleep(RELOAD_DELAY_S)
            self._reload.clear()
            with contextlib.suppress(Exception):
                await self.refresh_items()

    async def refresh_items(self) -> None:
        if self.state is None:
            return
        items = await self.read_items()
        self.apply(live_flow.apply_items(self.state, items, iso(now())))
        await self.read_spend()

    def apply(self, state: Json, *, reset: bool = False) -> None:
        """Take a new state; a card that is in another station than before has moved: it travels."""
        before = {} if reset or self.view is None else {c["id"]: c["lane"] for c in self.view["cards"]}
        self.state = state
        self.view = live_flow.view(state, iso(now()))
        for card in self.view["cards"]:
            old = before.get(card["id"])
            if old is not None and old != card["lane"]:
                self.moves[card["id"]] = (live_flow.edge_between(self.graph, old, card["lane"]), mono())
        if self.selected is None and self.view["cards"]:
            self.selected = self.view["cards"][0]["id"]
            self.post_message(self.Moved())
        self.redraw(force=True)

    # ──────────────────────────────────── drawing ────────────────────────────────────

    def motion_now(self) -> live_view.Motion:
        clock = mono()
        travel: dict[str, tuple[str, float]] = {}
        fresh: dict[str, float] = {}
        lit: set[str] = set()
        for card_id, (edge, started) in list(self.moves.items()):
            age = clock - started
            if age > HIGHLIGHT_S + TRAVEL_S:
                del self.moves[card_id]
                continue
            if self.motion and edge and age < TRAVEL_S:
                travel[card_id] = (edge, age / TRAVEL_S)
            fresh[card_id] = max(0.0, 1 - max(0.0, age - TRAVEL_S) / HIGHLIGHT_S)
            if edge and age < LIT_S:
                lit.add(edge)
        frame = int(clock / TICK_S) if self.motion else None
        return live_view.Motion(
            frame=frame, travel=travel, fresh=fresh, lit=frozenset(lit), selected=self.selected
        )

    def redraw(self, *, force: bool = False) -> None:
        if self.state is None:
            self.show_problem()
            return
        moment = now()
        self.view = live_flow.view(self.state, iso(moment))
        motion = self.motion_now()
        signature = (
            motion.frame,
            tuple((k, v[0], int(v[1] * 10)) for k, v in sorted(motion.travel.items())),
            tuple(sorted(motion.fresh)),
            motion.lit,
            motion.selected,
            int(moment.timestamp()),
            self.connection,
            self.size.width,
        )
        if not force and signature == self._signature:
            return
        self._signature = signature
        width = self.size.width or 100
        self.drawing = live_view.draw(
            self.view, self.graph, self.initial, width=width, motion=motion, ascii_only=self.ascii
        )
        flow = self.query_one("#flow", Static)
        flow.update(Text("\n").join(self.drawing.lines))
        hue = ""
        self.query_one("#flow-header", Static).update(
            live_view.header(
                str(self.project.get("name") or self.slug),
                self.workflow,
                self.connection,
                iso(moment),
                width,
                self.ascii,
                hue,
            )
        )
        self.query_one("#flow-summary", Static).update(live_view.summary(self.view, self.spend, width))
        ticker = Text("\n").join(live_view.ticker_lines(self.view, TICKER_ROWS, width, self.ascii))
        self.query_one("#flow-ticker", Static).update(ticker)
        self.follow_cursor()

    def show_problem(self) -> None:
        text = Text(self.error or "loading…", style="red" if self.error else "dim")
        with contextlib.suppress(Exception):
            self.query_one("#flow", Static).update(text)
            self.query_one("#flow-header", Static).update(
                live_view.header(
                    str(self.project.get("name") or self.slug),
                    self.workflow,
                    self.connection,
                    iso(now()),
                    self.size.width or 100,
                    self.ascii,
                )
            )

    def follow_cursor(self) -> None:
        if self.drawing is None or self.selected not in self.drawing.token_at:
            return
        x, y, w = self.drawing.token_at[self.selected]
        with contextlib.suppress(Exception):
            self.query_one("#flow-scroll", VerticalScroll).scroll_to_region(
                Region(max(0, x - 2), y, w + 4, 1), animate=False, spacing=None
            )

    def tick(self) -> None:
        if self.state is not None:
            self.redraw()
        else:
            self.show_problem()

    def on_resize(self) -> None:
        if self.state is not None:
            self.redraw(force=True)

    # ───────────────────────────────────── keys ─────────────────────────────────────

    def action_step(self, direction: int) -> None:
        if self.drawing is None or not self.drawing.order:
            return
        order = self.drawing.order
        index = order.index(self.selected) if self.selected in order else -1
        self.selected = order[(index + direction) % len(order)]
        self.post_message(self.Moved())
        self.redraw(force=True)

    def action_open_item(self) -> None:
        if self.view is None or self.selected is None:
            return
        card = next((c for c in self.view["cards"] if c["id"] == self.selected), None)
        if card is None:
            return
        display = next(
            (lane["display"] for lane in self.view["lanes"] if lane["state"] == card["lane"]), card["lane"]
        )
        self.app.push_screen(ItemScreen(self.api, card, str(display), ""))


class LiveApp(App[None]):
    """`choregos live`: the pane alone, full screen."""

    TITLE = "choregos live"
    ENABLE_COMMAND_PALETTE = False
    CSS = "Screen { layout: vertical; }"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        api: AsyncClient,
        organisation: str,
        project: Json,
        workflow: str,
        *,
        is_default: bool = False,
        motion: bool = True,
    ) -> None:
        super().__init__(ansi_color=True)
        self.api = api
        self.flow = LiveFlowPane(
            api,
            organisation,
            project,
            workflow,
            is_default=is_default,
            motion=motion,
            hint=" ←→ token   enter open   q quit",
        )

    def compose(self) -> ComposeResult:
        yield self.flow

    def on_mount(self) -> None:
        self.flow.focus()

    async def on_unmount(self) -> None:
        with contextlib.suppress(Exception):
            await self.api.close()


def run(
    api: AsyncClient,
    organisation: str,
    project: Json,
    workflow: str,
    *,
    is_default: bool,
    colour: bool,
    motion: bool,
) -> None:
    """Open the screen and return when the person quits it."""
    if not colour:
        os.environ["NO_COLOR"] = "1"  # Textual reads it as well: its renderer drops every colour
    LiveApp(api, organisation, project, workflow, is_default=is_default, motion=motion).run()
