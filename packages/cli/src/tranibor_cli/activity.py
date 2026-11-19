# SPDX-License-Identifier: Apache-2.0
"""`choregos activity`: the live tree of what runs in an organisation (ADR 0154 §2-3, ADR 0142 §1).

Project → work item → run (stage · agent · model), and the person's own agent sessions, each line with
its status (a glyph and a word, then a colour), its elapsed time, its tokens and its cost so far. A spend the
gateway cannot say is "—", never 0 (ADR 0141 §2). Then what ended in the last hour, so a stop is seen to have
taken effect. `--watch` keeps the tree live from the activity stream (`activity.upsert`, `activity.ended`)
until the stream ends or the person interrupts it.

It reads `GET /orgs/{org}/activity` and its stream, the sub-agents of each run (`GET /runs/{id}/agents`,
S67-12: the platform names them, this command never reads a backend's `_meta`), and nothing else but
today's spend for the status line (`GET /orgs/{org}/costs`, "—" for a person who may not read it). It is
read-only: stopping stays where it is today (`choregos items action … stop`, the console).
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

from . import money, theme
from .client import ApiError, Client, NoOrganisationError
from .live_view import reason_long

#: The statuses of an entry still in flight (`ActivityEntry.status`).
IN_FLIGHT = frozenset({"queued", "running", "open"})
#: What the list shows of what ended, as the API does (ADR 0142 §1).
FINISHED_SHOWN = 50
#: How often the live view redraws, so elapsed times move even when no event comes.
REDRAW_EVERY_S = 1.0
DASH = "—"
#: How long a run's sub-agents are kept before the live view asks again (a run upserts at every step).
AGENTS_EVERY_S = 3.0


def clock() -> datetime:
    """Now, in UTC: what elapsed times are measured against (the tests pin it)."""
    return datetime.now(UTC)


@dataclass
class Board:
    """What the tree shows: the entries in flight and those that ended, by id, kept in the API's order."""

    in_flight: dict[str, dict[str, Any]] = field(default_factory=dict)
    finished: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: A run's agent nodes (`RunAgentNode`), by run id, and when each list was read.
    agents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    agents_read: dict[str, datetime] = field(default_factory=dict)

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

    def read_agents(self, api: Client, now: datetime) -> None:
        """Asks for the sub-agents of each run shown: an ended run once, a run in flight again after
        `AGENTS_EVERY_S`. A run whose nodes cannot be read is shown without them, never with a failure."""
        for entry in [*self.in_flight.values(), *self.finished.values()]:
            run_id = str(entry.get("id"))
            if entry.get("kind") != "run":
                continue
            read = self.agents_read.get(run_id)
            live = entry.get("status") in IN_FLIGHT
            if read is not None and not (live and (now - read).total_seconds() >= AGENTS_EVERY_S):
                continue
            self.agents_read[run_id] = now
            try:
                listing = api.get(f"/runs/{run_id}/agents")
            except ApiError:
                continue
            self.agents[run_id] = list((listing or {}).get("items") or [])

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
    run: Annotated[
        str | None, typer.Option("--run", help="one run's steps instead: its plan, tool calls and context")
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="with --run: one JSON object, nothing drawn")
    ] = False,
) -> None:
    """What runs in the organisation, as a tree: project, work item, run, with status, time, tokens and cost.

    Read-only. `--watch` follows the activity stream; elapsed times move every second. `--run ID` shows one
    run's own steps (the agent's plan, its tool calls with state and time, what fills its context); with
    `--json` they are one JSON object for a script (not with `--watch`).
    """
    from . import main

    api: Client = main.TransportSafeClient()
    if as_json and not run:
        main.fail("--json is for --run: the tree is read in the terminal")
        return
    if run:
        from . import run_activity

        if project or workflow or agent or kind:
            main.fail("--run shows one run: it takes no --project, --workflow, --agent or --kind")
            return
        if as_json and watch:
            main.fail("--json prints once: it does not combine with --watch")
            return
        try:
            if as_json:
                typer.echo(run_activity.as_json(api, run))
            else:
                run_activity.show(api, run, watch=watch, out=theme.console())
        except ApiError as exc:
            main.fail(str(exc))
        return
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
        board.read_agents(api, clock())
        today = _today_spend(api, organisation)
        pricing = money.Pricing(api, organisation)
        if not watch:
            out.print(render(board, organisation, today, out, pricing=pricing))
            return
        _follow(api, organisation, filters, board, today, out, pricing)
    except ApiError as exc:
        main.fail(str(exc))


def _today_spend(api: Client, organisation: str) -> money.Amount | None:
    """The organisation's spend today, as the report gives it (dollars and euros); `None` for a person who
    may not read it (a project member)."""
    day = date.today().isoformat()
    try:
        report = api.get(f"/orgs/{organisation}/costs", params={"since": day, "until": day})
    except ApiError:
        return None
    return money.amount((report or {}).get("total"))


def _follow(
    api: Client,
    organisation: str,
    filters: dict[str, str],
    board: Board,
    today: money.Amount | None,
    out: Console,
    pricing: money.Pricing | None = None,
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
        with Live(
            render(board, organisation, today, out, pricing=pricing), console=out, auto_refresh=False
        ) as live:
            for item in _drain(events):
                if item is not None:
                    board.apply(*item)
                board.read_agents(api, clock())
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


def render(
    board: Board,
    organisation: str,
    today: money.Amount | float | None,
    out: Console,
    *,
    pricing: money.Pricing | None = None,
) -> RenderableType:
    """The tree in its blue frame, what ended below it, the status line last."""
    now = clock()
    parts: list[RenderableType] = [
        Panel(
            _tree(
                list(board.in_flight.values()), now, out, "nothing is running", board.agents, pricing=pricing
            ),
            title=Text("In flight", style="bold"),
            title_align="left",
            border_style=theme.TREE_FRAME,
        )
    ]
    if board.finished:
        parts.append(
            _tree(
                list(board.finished.values()),
                now,
                out,
                "",
                board.agents,
                label="Ended in the last hour",
                pricing=pricing,
            )
        )
    parts.append(status_line(board, organisation, today, currency=pricing.currency if pricing else None))
    return Group(*parts)


def status_line(
    board: Board,
    organisation: str,
    today: money.Amount | float | None,
    waiting: int | None = None,
    *,
    currency: str | None = None,
) -> Text:
    """`acme · 3 running · 2 waiting for you · €1.24 today`: `waiting` is what `GET /me/pending` lists, "—"
    where it is not read (`choregos activity` reads no such list). `today` is an `Amount` (the organisation's
    `currency` picks the figure); a bare number is dollars, as the gateway counts them."""
    if isinstance(today, int | float):
        today = money.Amount(usd=float(today))
    spent = DASH if today is None else today.text(currency)
    count = DASH if waiting is None else str(waiting)
    return Text(f"{organisation} · {board.running()} running · {count} waiting for you · {spent} today")


def _tree(
    entries: list[dict[str, Any]],
    now: datetime,
    out: Console,
    empty: str,
    agents: dict[str, list[dict[str, Any]]],
    *,
    label: str = "",
    pricing: money.Pricing | None = None,
) -> Tree:
    root = Tree(
        Text(label, style="bold") if label else Text(""), hide_root=not label, guide_style=theme.TREE_FRAME
    )
    if not entries:
        root.add(Text(empty))
        return root
    grouped = group(entries)
    for project, work_items in grouped.projects.items():
        branch = root.add(Text(project, style=f"bold {theme.hue_style(project)}"))
        for runs in work_items.values():
            item = branch.add(work_item_label(runs[0]))
            for entry in runs:
                _sub_agents(
                    item.add(line(entry, now, out, pricing=pricing)),
                    agents.get(str(entry.get("id"))) or [],
                    now,
                    out,
                )
    if grouped.sessions:
        sessions = root.add(Text("your sessions and conversations", style="bold"))
        for entry in grouped.sessions:
            sessions.add(line(entry, now, out, pricing=pricing))
    return root


def sub_agents(nodes: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """The run's sub-agent nodes, each with the sub-agent node it was launched from (`None`: from the run's
    own agent), parents first. The run's own agent is the run's line, and a run with no sub-agent (a backend
    that names no parent included) gives nothing. The tree here and the workspace's draw the same shape."""
    placed: dict[str, dict[str, Any]] = {}
    found: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for node in nodes:
        if node.get("kind") != "subagent":
            continue
        found.append((node, placed.get(str(node.get("parent_id")))))
        placed[str(node["id"])] = node
    return found


def _sub_agents(under: Tree, nodes: list[dict[str, Any]], now: datetime, out: Console) -> None:
    by_id: dict[str, Tree] = {}
    for node, parent in sub_agents(nodes):
        holder = by_id[str(parent["id"])] if parent else under
        by_id[str(node["id"])] = holder.add(agent_line(node, now, out))


def agent_line(node: dict[str, Any], now: datetime, out: Console) -> Text:
    """`● running  sub-agent Agent · 3 tool calls   1m04s`."""
    text = theme.badge(theme.status_of(str(node.get("status") or "")), out)
    calls = int(node.get("tool_calls") or 0)
    text.append(f"  sub-agent {node.get('tool') or DASH} · {calls} tool call{'' if calls == 1 else 's'}")
    text.append(f"   {elapsed(node, now)}")
    text.no_wrap, text.overflow = True, "ellipsis"
    return text


@dataclass
class Grouped:
    """The tree's shape, without its drawing: runs by project then work item, and the person's own
    sessions and turns. The workspace (`choregos chat`) draws the same shape with Textual."""

    projects: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    sessions: list[dict[str, Any]] = field(default_factory=list)


def work_item_of(entry: dict[str, Any]) -> str:
    return str(entry.get("work_item_key") or entry.get("work_item_id") or DASH)


def group(entries: list[dict[str, Any]]) -> Grouped:
    """Project → work item → run, in the order the entries come; what is not a run of a project goes
    under the person's sessions."""
    grouped = Grouped()
    for entry in entries:
        project = entry.get("project_slug")
        if entry.get("kind") != "run" or not project:
            grouped.sessions.append(entry)
            continue
        grouped.projects.setdefault(project, {}).setdefault(work_item_of(entry), []).append(entry)
    return grouped


def work_item_label(entry: dict[str, Any]) -> Text:
    text = Text(work_item_of(entry), style="bold")
    if entry.get("work_item_title"):
        text.append(f"  {entry['work_item_title']}")
    workflow = entry.get("workflow")
    if workflow:
        text.append("  ")
        text.append(str(workflow), style=theme.hue_style(str(workflow)))
    return text


def line(
    entry: dict[str, Any],
    now: datetime,
    out: Console,
    *,
    compact: bool = False,
    pricing: money.Pricing | None = None,
) -> Text:
    """`● running  implement · coder · sonnet-4.5   4m12s · 12.3k in / 800 out · $0.84   run 0192a3b4c5d6`.

    `compact` (the workspace's half-screen tree) leaves the model and the id out: the rest is the same."""
    text = theme.badge(theme.status_of(str(entry.get("status") or "")), out)
    what = [entry.get("stage_role") or entry.get("kind"), entry.get("agent_slug")]
    if not compact:
        what.append(model_name(entry.get("model")))
    text.append("  " + " · ".join(str(w) for w in what if w))
    text.append(f"   {elapsed(entry, now)} · {spend(entry.get('spend'), pricing)}")
    failure = entry.get("failure")
    if entry.get("status") == "failed" and failure:
        # The reason as a word, never the provider's body (ADR 0187 §3): the summary is for the detail pane.
        text.append(f" · {reason_long(failure.get('reason'))}", style="red")
    if not compact:
        text.append(f"   {entry.get('kind')} {str(entry.get('id'))[:12]}")
    # One line per entry: on a narrow terminal the id is cut, never the status, the time or the spend.
    text.no_wrap, text.overflow = True, "ellipsis"
    return text


def model_name(model: Any) -> str | None:
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


def spend(value: Any, pricing: money.Pricing | None = None) -> str:
    """Tokens and cost so far; "—" when the gateway cannot say (ADR 0141 §2): unknown is never shown as 0.
    A run's spend is the gateway's, in dollars: converted to the organisation's currency by `pricing` when
    it has a rate, else printed with its own symbol."""
    if not isinstance(value, dict):
        return DASH
    return (
        f"{_count(value.get('tokens_in'))} in / {_count(value.get('tokens_out'))} out · "
        f"{pricing.text(money.amount(value)) if pricing else money.amount(value).text(None)}"
    )
