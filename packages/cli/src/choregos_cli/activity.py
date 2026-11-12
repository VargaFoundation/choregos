# SPDX-License-Identifier: Apache-2.0
"""`choregos activity`: the live tree of what runs in an organisation (ADR 0154 §2-3, ADR 0142 §1).

Project → work item → run (stage · agent · model), and the person's own agent sessions, each line with
its status (a glyph and a word, then a colour), its elapsed time, its tokens and its cost so far. A spend the
gateway cannot say is "—", never 0 (ADR 0141 §2). Then what ended in the last hour, so a stop is seen to have
taken effect. `--watch` keeps the tree live from the activity stream (`activity.upsert`, `activity.ended`)
until the stream ends or the person interrupts it.

It reads `GET /orgs/{org}/activity` and its stream, and nothing else but today's spend for the status line
(`GET /orgs/{org}/costs`, "—" for a person who may not read it). It is read-only: stopping stays where it is
today (`choregos items action … stop`, the console).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any

import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

from . import theme
from .client import ApiError, Client, NoOrganisationError

#: The statuses of an entry still in flight (`ActivityEntry.status`).
IN_FLIGHT = frozenset({"queued", "running", "open"})
#: What the list shows of what ended, as the API does (ADR 0142 §1).
FINISHED_SHOWN = 50
#: How often the live view redraws, so elapsed times move even when no event comes.
REDRAW_EVERY_S = 1.0
DASH = "—"


def clock() -> datetime:
    """Now, in UTC: what elapsed times are measured against (the tests pin it)."""
    return datetime.now(UTC)


@dataclass
class Board:
    """What the tree shows: the entries in flight and those that ended, by id, kept in the API's order."""

    in_flight: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def of(cls, listing: dict[str, Any]) -> Board:
        board = cls()
        for entry in listing.get("in_flight") or []:
            board.in_flight[str(entry["id"])] = entry
        for entry in listing.get("finished") or []:
            board.finished[str(entry["id"])] = entry
        return board

    def apply(self, event: str, entry: dict[str, Any]) -> None:
        """An `activity.upsert` puts the entry in flight (newest first); an `activity.ended` moves it to what
        ended (newest first), whatever status it carries."""
        key = str(entry.get("id") or "")
        if not key:
            return
        if event == "activity.upsert" and entry.get("status") in IN_FLIGHT:
            self.finished.pop(key, None)
            known = key in self.in_flight
            self.in_flight[key] = entry
            if not known:
                self.in_flight = {key: entry} | {k: v for k, v in self.in_flight.items() if k != key}
        elif event in ("activity.upsert", "activity.ended"):
            self.in_flight.pop(key, None)
            self.finished = {key: entry} | {k: v for k, v in self.finished.items() if k != key}
            self.finished = dict(list(self.finished.items())[:FINISHED_SHOWN])

    def running(self) -> int:
        return sum(1 for entry in self.in_flight.values() if entry.get("status") in ("running", "open"))


def activity(
    watch: Annotated[
        bool, typer.Option("--watch", "-w", help="keep the tree live until the stream ends or Ctrl-C")
    ] = False,
    project: Annotated[str | None, typer.Option(help="only this project (its slug)")] = None,
    workflow: Annotated[str | None, typer.Option(help="only the work items born in this workflow")] = None,
    agent: Annotated[str | None, typer.Option(help="only this agent (its slug)")] = None,
    kind: Annotated[str | None, typer.Option(help="run, session or turn")] = None,
    org: Annotated[
        str | None, typer.Option("--org", help="the organisation; the profile's otherwise")
    ] = None,
) -> None:
    """What runs in the organisation, as a tree: project, work item, run, with status, time, tokens and cost.

    Read-only. `--watch` follows the activity stream; elapsed times move every second.
    """
    from . import main

    api: Client = main.TransportSafeClient()
    try:
        organisation = api.profile.organisation(org)
    except NoOrganisationError as exc:
        main.fail(str(exc))
        return
    filters = {
        k: v for k, v in {"project": project, "workflow": workflow, "agent": agent, "kind": kind}.items() if v
    }
    out = theme.console()
    try:
        board = Board.of(api.get(f"/orgs/{organisation}/activity", params=filters))
        today = _today_spend(api, organisation)
        if not watch:
            out.print(render(board, organisation, today, out))
            return
        _follow(api, organisation, filters, board, today, out)
    except ApiError as exc:
        main.fail(str(exc))


def _today_spend(api: Client, organisation: str) -> float | None:
    """The organisation's spend today, in USD; `None` for a person who may not read it (a project member)."""
    day = date.today().isoformat()
    try:
        report = api.get(f"/orgs/{organisation}/costs", params={"since": day, "until": day})
    except ApiError:
        return None
    total = (report or {}).get("total") or {}
    value = total.get("cost_usd")
    return float(value) if isinstance(value, int | float) else None


def _follow(
    api: Client,
    organisation: str,
    filters: dict[str, str],
    board: Board,
    today: float | None,
    out: Console,
) -> None:
    """Apply the stream's events as they come, redrawing at least every second; stop when the stream ends."""
    events: queue.Queue[tuple[str, dict[str, Any]] | BaseException | None] = queue.Queue()

    def read() -> None:
        try:
            for item in api.stream_events("GET", f"/orgs/{organisation}/activity/stream", params=filters):
                events.put(item)
        except BaseException as exc:  # the reader's failure is the command's, raised in the main thread
            events.put(exc)
        finally:
            events.put(None)

    threading.Thread(target=read, name="choregos-activity-stream", daemon=True).start()
    try:
        with Live(render(board, organisation, today, out), console=out, auto_refresh=False) as live:
            for item in _drain(events):
                if item is not None:
                    board.apply(*item)
                live.update(render(board, organisation, today, out), refresh=True)
    except KeyboardInterrupt:
        return


def _drain(
    events: queue.Queue[tuple[str, dict[str, Any]] | BaseException | None],
) -> Iterator[tuple[str, dict[str, Any]] | None]:
    """Each event as it comes, or `None` every second without one (a redraw); ends with the stream."""
    while True:
        try:
            item = events.get(timeout=REDRAW_EVERY_S)
        except queue.Empty:
            yield None
            continue
        if item is None:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


def render(board: Board, organisation: str, today: float | None, out: Console) -> RenderableType:
    """The tree in its blue frame, what ended below it, the status line last."""
    now = clock()
    parts: list[RenderableType] = [
        Panel(
            _tree(list(board.in_flight.values()), now, out, "nothing is running"),
            title=Text("In flight", style="bold"),
            title_align="left",
            border_style=theme.TREE_FRAME,
        )
    ]
    if board.finished:
        parts.append(_tree(list(board.finished.values()), now, out, "", label="Ended in the last hour"))
    parts.append(status_line(board, organisation, today))
    return Group(*parts)


def status_line(board: Board, organisation: str, today: float | None) -> Text:
    """`acme · 3 running · — waiting for you · $1.24 today`: what waits for the person has no route yet
    (ADR 0154 G8), so it reads "—"."""
    spent = DASH if today is None else f"${today:.2f}"
    return Text(f"{organisation} · {board.running()} running · {DASH} waiting for you · {spent} today")


def _tree(entries: list[dict[str, Any]], now: datetime, out: Console, empty: str, *, label: str = "") -> Tree:
    root = Tree(
        Text(label, style="bold") if label else Text(""), hide_root=not label, guide_style=theme.TREE_FRAME
    )
    if not entries:
        root.add(Text(empty))
        return root
    projects: dict[str, Tree] = {}
    items: dict[tuple[str, str], Tree] = {}
    sessions: Tree | None = None
    for entry in entries:
        project = entry.get("project_slug")
        if entry.get("kind") != "run" or not project:
            if sessions is None:
                sessions = root.add(Text("your sessions and conversations", style="bold"))
            sessions.add(_line(entry, now, out))
            continue
        if project not in projects:
            projects[project] = root.add(Text(project, style=f"bold {theme.hue_style(project)}"))
        key = entry.get("work_item_key") or entry.get("work_item_id") or DASH
        if (project, key) not in items:
            items[(project, key)] = projects[project].add(_work_item(entry))
        items[(project, key)].add(_line(entry, now, out))
    return root


def _work_item(entry: dict[str, Any]) -> Text:
    text = Text(str(entry.get("work_item_key") or entry.get("work_item_id") or DASH), style="bold")
    workflow = entry.get("workflow")
    if workflow:
        text.append("  ")
        text.append(str(workflow), style=theme.hue_style(str(workflow)))
    return text


def _line(entry: dict[str, Any], now: datetime, out: Console) -> Text:
    """`● running  implement · coder · sonnet-4.5   4m12s · 12.3k in / 800 out · $0.84   run 0192a3b4c5d6`."""
    text = theme.badge(theme.status_of(str(entry.get("status") or "")), out)
    what = [entry.get("stage_role") or entry.get("kind"), entry.get("agent_slug"), _model(entry.get("model"))]
    text.append("  " + " · ".join(str(w) for w in what if w))
    text.append(f"   {elapsed(entry, now)} · {spend(entry.get('spend'))}")
    text.append(f"   {entry.get('kind')} {str(entry.get('id'))[:12]}")
    # One line per entry: on a narrow terminal the id is cut, never the status, the time or the spend.
    text.no_wrap, text.overflow = True, "ellipsis"
    return text


def _model(model: Any) -> str | None:
    return str(model).split("/")[-1] if model else None


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def elapsed(entry: dict[str, Any], now: datetime) -> str:
    """Since it started, to its end or to now: `12s`, `4m12s`, `1h03m`."""
    started = _when(entry.get("started_at"))
    if started is None:
        return DASH
    seconds = max(0, int(((_when(entry.get("ended_at")) or now) - started).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _count(tokens: Any) -> str:
    value = int(tokens or 0)
    return (
        f"{value / 1_000_000:.1f}M"
        if value >= 1_000_000
        else f"{value / 1000:.1f}k"
        if value >= 1000
        else str(value)
    )


def spend(value: Any) -> str:
    """Tokens and cost so far; "—" when the gateway cannot say (ADR 0141 §2): unknown is never shown as 0."""
    if not isinstance(value, dict):
        return DASH
    return (
        f"{_count(value.get('tokens_in'))} in / {_count(value.get('tokens_out'))} out · "
        f"${float(value.get('cost_usd') or 0):.2f}"
    )
