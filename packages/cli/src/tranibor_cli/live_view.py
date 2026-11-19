# SPDX-License-Identifier: Apache-2.0
"""`choregos live`'s drawing (S67-28, ADR 0187 §4): a workflow's flow as text, from the shared reduction.

Pure: no Textual, no clock, no network. It takes what `live_flow.view` gives (lanes, cards, ticker) and the
workflow's validated graph, and draws the same flow the console draws (`apps/web/src/components/workflows/
flow/`): the nominal path as stations from left to right, the side states (rejections, retries, escalations,
human waits) on a row below, a station's items parked in it as tokens, the transitions as arrows. The layout
(which state is on the path, which beside it, where each side state falls in from) is a port of the map's own
reading (`layoutMap`), so the terminal, the map and the flow never disagree about a workflow's shape.

The drawing is a grid of cells, so that an arrow has cells a token can step along. What moves (a token on an
arrow, a spinner, a token that just arrived) is passed in as a `Motion`; without one the drawing is still.
Glyph and word first, colour second (ADR 0154 §9): a failed token says `✗` and why, a waiting one `⏸` and what
it waits for, whatever the terminal's colours; an ASCII mode draws the same with `+ - |`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rich.text import Text

Json = dict[str, Any]

FULL_W = 24
COLLAPSED_W = 16
GAP = 7
COLLAPSED_GAP = 5
SIDE_GAP = 4
MAX_TOKENS = 5
MARGIN = 1
#: Beyond this, the enumeration of nominal paths stops: a heavily meshed workflow must not freeze the screen.
MAX_VISITS = 20_000

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"

#: Who moves the work on from a state: the colour of its box (`apps/web/.../map/layout.ts`, `KIND_COLOURS`).
KIND_STYLE = {
    "agent": "magenta",
    "human": "cyan",
    "platform": "bright_black",
    "train": "green",
    "decision": "bright_cyan",
    "waiting": "yellow",
    "end": "bright_black",
}
KIND_WORD = {
    "agent": "agent",
    "human": "human",
    "platform": "platform",
    "train": "release train",
    "decision": "decision",
    "waiting": "waiting",
    "end": "end",
}
_LANE_KIND = {
    "agent": "agent",
    "human": "human",
    "system": "platform",
    "train": "train",
    "decision": "decision",
    "wait": "waiting",
    "terminal": "end",
}

#: A failure's reason as a word that fits a token (`ActivityEntry.failure.reason`, ADR 0187 §3).
REASON_WORD = {
    "loop": "loop",
    "budget": "budget",
    "max_turns": "turns",
    "timeout": "timeout",
    "timed_out": "timeout",
    "gateway": "gateway",
    "workflow": "stalled",
    "failed": "failed",
}
REASON_LONG = {
    "loop": "loop",
    "budget": "budget spent",
    "max_turns": "too many tool calls",
    "timeout": "timed out",
    "timed_out": "timed out",
    "gateway": "gateway refused",
    "workflow": "the workflow stopped",
    "failed": "failed",
}

BOX = {
    "light": "╭╮╰╯─│",
    "heavy": "┏┓┗┛━┃",
    "double": "╔╗╚╝═║",
    "ascii": "++++-|",
}


def reason_word(reason: str | None) -> str:
    text = reason or "failed"
    return REASON_WORD.get(text, text.replace("_", " ")[:8])


def reason_long(reason: str | None) -> str:
    text = reason or "failed"
    return REASON_LONG.get(text, text.replace("_", " "))


# ────────────────────────────────── the layout (a port of the map's) ──────────────────────────────────


def _is_nominal(edge: Json) -> bool:
    return not edge.get("kind") or edge["kind"] == "nominal"


def nominal_path(graph: Json, initial: str | None = None) -> list[str]:  # noqa: C901
    """The longest simple run of nominal transitions from the initial state (`nominalPath` of the map).

    Without a declared initial state: the one nothing reaches and a transition leaves, else the first one a
    transition leaves, else the first. At equal length the path that ends on a terminal state, then the YAML
    order.
    """
    nodes = graph.get("nodes") or []
    if not nodes:
        return []
    rank = {n["id"]: i for i, n in enumerate(nodes)}
    terminal = {n["id"] for n in nodes if n.get("terminal")}
    edges = graph.get("edges") or []
    nexts: dict[str, list[str]] = {}
    for edge in edges:
        if not _is_nominal(edge) or edge.get("wildcard") or edge["to"] not in rank:
            continue
        targets = nexts.setdefault(edge["from"], [])
        if edge["to"] not in targets:
            targets.append(edge["to"])
    reached = {e["to"] for e in edges if e.get("kind") != "default"}
    if initial and initial in rank:
        start = initial
    else:
        start = next(
            (n["id"] for n in nodes if n["id"] not in reached and n["id"] in nexts),
            next((n["id"] for n in nodes if n["id"] in nexts), nodes[0]["id"]),
        )
    best = [start]

    def better(a: list[str], b: list[str]) -> bool:
        if len(a) != len(b):
            return len(a) > len(b)
        ta, tb = a[-1] in terminal, b[-1] in terminal
        if ta != tb:
            return ta
        for x, y in zip(a, b, strict=True):
            if rank[x] != rank[y]:
                return rank[x] < rank[y]
        return False

    visits = 0
    path = [start]
    seen = {start}

    def explore(current: str) -> None:
        nonlocal best, visits
        visits += 1
        if visits > MAX_VISITS:
            return
        if better(path, best):
            best = list(path)
        for nxt in nexts.get(current, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            path.append(nxt)
            explore(nxt)
            path.pop()
            seen.discard(nxt)

    explore(start)
    return best


def secondary_label(edge: Json) -> str:
    """A secondary edge's label as the map says it: only the retry bound is rephrased."""
    raw = (edge.get("label") or "").strip()
    if raw.startswith("on failure"):
        import re

        bound = re.search(r"\(\s*≤\s*(\d+)\s*\)", raw)
        return f"on failure (up to {bound.group(1)} retries)" if bound else "on failure"
    return raw or edge.get("kind") or ""


def how_reached(edge: Json) -> str:
    kind = edge.get("kind")
    if kind == "reject":
        return "if rejected"
    if kind in ("retry", "escalate", "resume"):
        return secondary_label(edge)
    return (edge.get("label") or "").strip()


@dataclass
class Box:
    id: str
    display: str
    kind: str
    who: str
    terminal: bool
    lane_kind: str | None
    outside: bool
    side: bool
    cards: list[str]
    count: int
    closed_today: int
    x: int = 0
    y: int = 0
    w: int = FULL_W
    h: int = 0


@dataclass
class Route:
    """An arrow as the cells a token steps along, from its source's border to its target's."""

    id: str
    source: str
    target: str
    cells: list[tuple[int, int]]
    nominal: bool


@dataclass
class Drawing:
    lines: list[Text]
    width: int
    height: int
    #: card id -> (x, y, width) of its row, for the cursor to scroll to.
    token_at: dict[str, tuple[int, int, int]]
    routes: dict[str, Route]
    #: Every card, in the order the cursor walks them: the path's stations, then the side states'.
    order: list[str]
    collapsed: bool
    boxes: dict[str, Box] = field(default_factory=dict)


@dataclass(frozen=True)
class Motion:
    """What moves at this instant. Without one the drawing is still."""

    #: Spinner frame of a running token; `None` draws a static `●` (reduced motion).
    frame: int | None = None
    #: card id -> (edge id, progress 0..1): a token on its arrow.
    travel: dict[str, tuple[str, float]] = field(default_factory=dict)
    #: card id -> strength 0..1 of the highlight of a token that just arrived.
    fresh: dict[str, float] = field(default_factory=dict)
    #: Edges lit: one a token just took.
    lit: frozenset[str] = frozenset()
    selected: str | None = None


# ────────────────────────────────────── the canvas ──────────────────────────────────────


class Canvas:
    def __init__(self, width: int, height: int, ascii_only: bool) -> None:
        self.width, self.height = width, height
        self.cells: list[list[tuple[str, str]]] = [[(" ", "")] * width for _ in range(height)]
        self.ascii = ascii_only

    def put(self, x: int, y: int, char: str, style: str = "", *, cross: bool = False) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        if self.ascii:
            char = {"…": "~", "·": "-"}.get(char, char)
        if cross:
            before = self.cells[y][x][0]
            if (before in "─┄" and char in "│┆") or (before in "│┆" and char in "─┄"):
                char = "+" if self.ascii else "┼"
        self.cells[y][x] = (char, style)

    def text(self, x: int, y: int, value: str, style: str = "") -> None:
        for offset, char in enumerate(value):
            self.put(x + offset, y, char, style)

    def blank(self, x: int, y: int, length: int) -> bool:
        return all(0 <= x + i < self.width and self.cells[y][x + i][0] == " " for i in range(length))

    def lines(self) -> list[Text]:
        out: list[Text] = []
        for row in self.cells:
            line = Text(no_wrap=True, overflow="crop")
            run, style = "", None
            for char, st in row:
                if st != style and run:
                    line.append(run, style=style or None)
                    run = ""
                style = st
                run += char
            if run:
                line.append(run, style=style or None)
            line.rstrip()
            out.append(line)
        return out


def _fit(value: str, width: int) -> str:
    return value if len(value) <= width else value[: max(0, width - 1)] + "…"


def mmss(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}:{seconds % 60:02d}"


# ────────────────────────────────────── stations ──────────────────────────────────────


def _boxes(view: Json, graph: Json | None, initial: str | None) -> tuple[list[Box], list[Json]]:
    """The stations in drawing order (the path, then the side states) and the edges worth drawing."""
    lanes = {lane["state"]: lane for lane in view["lanes"]}
    nodes = {n["id"]: n for n in (graph or {}).get("nodes", [])}
    edges: list[Json] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in (graph or {}).get("edges", []):
        pair = (edge["from"], edge["to"])
        if (
            edge.get("kind") == "default"
            or edge.get("wildcard")
            or edge["from"] == edge["to"]
            or edge["from"] not in nodes
            or edge["to"] not in nodes
            or pair in seen_pairs
        ):
            continue
        seen_pairs.add(pair)
        edges.append(edge)

    def box_of(state: str, side: bool) -> Box:
        lane = lanes.get(state) or {"display": state, "count": 0, "cards": [], "closed_today": 0}
        node = nodes.get(state)
        if node is None:
            kind, who = "platform", "not in the workflow"
        else:
            actor_lane = node.get("lane") or node.get("kind")
            kind = "end" if node.get("terminal") else _LANE_KIND.get(actor_lane, "platform")
            actor = next(
                (
                    e.get("actor")
                    for e in (graph or {}).get("edges", [])
                    if e["from"] == state
                    and e.get("actor")
                    and not e.get("wildcard")
                    and e.get("kind") != "default"
                ),
                None,
            )
            who = str(actor) if actor and kind != "end" else KIND_WORD[kind]
        return Box(
            id=state,
            display=str(lane.get("display") or (node or {}).get("display") or state),
            kind=kind,
            who=who,
            terminal=bool(node and node.get("terminal")),
            lane_kind=lane.get("kind"),
            outside=node is None,
            side=side,
            cards=list(lane.get("cards") or []),
            count=int(lane.get("count") or 0),
            closed_today=int(lane.get("closed_today") or 0),
        )

    if graph is None or not nodes:
        return [box_of(state, False) for state in lanes], []
    path = nominal_path(graph, initial)
    on_path = set(path)
    rank = {nid: i for i, nid in enumerate(nodes)}
    position = {nid: i for i, nid in enumerate(path)}

    def anchor(state: str) -> float:
        incoming = [e for e in edges if e["to"] == state and e["from"] != state]
        direct = [e for e in incoming if e["from"] in on_path]
        if direct:
            return min(position[e["from"]] for e in direct)
        via = [e for e in incoming if e["from"] not in on_path]
        if via:
            parents = [e for e in edges if e["to"] == via[0]["from"] and e["from"] in on_path]
            return (position[parents[0]["from"]] if parents else len(path)) + 0.5
        return len(path) + 1

    sides = sorted((n for n in nodes if n not in on_path), key=lambda n: (anchor(n), rank[n]))
    outside = [s for s in lanes if s not in nodes]
    boxes = [box_of(s, False) for s in path] + [box_of(s, True) for s in (*sides, *outside)]
    return boxes, edges


def _tokens_of(
    box: Box, cards: dict[str, Json], selected: str | None, fresh: dict[str, float]
) -> tuple[list[str], int]:
    """The cards a station shows (at most `MAX_TOKENS`, the last place saying how many more) and how many
    are left out. What matters is kept: the cursor's card, a card that just arrived, then what is not idle."""
    ids = box.cards
    if len(ids) <= MAX_TOKENS:
        return ids, 0
    rank = {"failed": 0, "running": 1, "waiting": 2, "idle": 3}

    def priority(index_id: tuple[int, str]) -> tuple[int, int, int]:
        index, cid = index_id
        special = 0 if cid == selected or cid in fresh else 1
        return (special, rank.get(cards[cid]["status"], 3), index)

    keep = sorted(enumerate(ids), key=priority)[: MAX_TOKENS - 1]
    shown = [cid for _, cid in sorted(keep)]
    return shown, len(ids) - len(shown)


def _box_height(
    box: Box, collapsed: bool, cards: dict[str, Json], selected: str | None, fresh: dict[str, float]
) -> int:
    if collapsed:
        return 5
    shown, more = _tokens_of(box, cards, selected, fresh)
    return 4 + max(2, len(shown) + (1 if more else 0))


def _border_style(box: Box, cards: dict[str, Json]) -> tuple[str, str]:
    """The box's style and which border it wears: failed is double and red, in flight is heavy and blue, what
    waits for a person is amber, the rest wears its actor's colour."""
    statuses = {cards[c]["status"] for c in box.cards}
    if "failed" in statuses:
        return "red", "double"
    if "running" in statuses:
        return "bold blue", "heavy"
    if "waiting" in statuses:
        return "yellow", "light"
    return KIND_STYLE.get(box.kind, ""), "light"


# ────────────────────────────────────────── drawing ──────────────────────────────────────────


@dataclass
class _Ports:
    """Where an arrow leaves or enters a station's top or bottom border: each gets its own column."""

    used: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))

    def take(self, box: Box, side: str) -> int:
        n = self.used[(box.id, side)]
        self.used[(box.id, side)] += 1
        spread = 2 if box.w >= FULL_W else 1
        offset = ((n + 1) // 2) * spread * (1 if n % 2 else -1) if n else 0
        reach = box.w // 2 - 2
        offset = max(-reach, min(reach, offset))
        return box.x + box.w // 2 + offset


def draw(
    view: Json,
    graph: Json | None,
    initial: str | None,
    *,
    width: int,
    motion: Motion | None = None,
    ascii_only: bool = False,
) -> Drawing:
    """The flow at `width` columns: full stations when they fit, counts when they do not, and wider than
    `width` when even counts do not (the screen then scrolls)."""
    motion = motion or Motion()
    full = _draw(view, graph, initial, motion, ascii_only, collapsed=False)
    if full.width <= width:
        return full
    return _draw(view, graph, initial, motion, ascii_only, collapsed=True)


def _draw(  # noqa: C901, PLR0912, PLR0915
    view: Json, graph: Json | None, initial: str | None, motion: Motion, ascii_only: bool, *, collapsed: bool
) -> Drawing:
    cards = {c["id"]: c for c in view["cards"]}
    boxes, edges = _boxes(view, graph, initial)
    by_id = {b.id: b for b in boxes}
    path = [b for b in boxes if not b.side]
    sides = [b for b in boxes if b.side]
    box_w = COLLAPSED_W if collapsed else FULL_W
    gap = COLLAPSED_GAP if collapsed else GAP
    side_gap = 3 if collapsed else SIDE_GAP
    for box in boxes:
        box.w = box_w
        box.h = _box_height(box, collapsed, cards, motion.selected, motion.fresh)
    path_h = max((b.h for b in path), default=0)
    side_h = max((b.h for b in sides), default=0)
    for box in path:
        box.h = path_h
    for box in sides:
        box.h = side_h
    for i, box in enumerate(path):
        box.x = MARGIN + i * (box_w + gap)
    path_index = {b.id: i for i, b in enumerate(path)}
    side_index = {b.id: i for i, b in enumerate(sides)}

    # Classify the arrows.
    inline: list[Json] = []  # path -> the next path station
    arcs_top: list[Json] = []  # path -> any other path station: over the row
    down: list[Json] = []  # path <-> side
    side_inline: list[Json] = []
    arcs_bottom: list[Json] = []
    for edge in edges:
        a, b = edge["from"], edge["to"]
        if a in path_index and b in path_index:
            (inline if path_index[b] == path_index[a] + 1 else arcs_top).append(edge)
        elif a in side_index and b in side_index:
            (side_inline if side_index[b] == side_index[a] + 1 else arcs_bottom).append(edge)
        else:
            down.append(edge)

    # Side stations hang under the first path station they are reached from, never over their neighbour.
    next_x = MARGIN
    for box in sides:
        anchors = [path_index[e["from"]] for e in down if e["to"] == box.id and e["from"] in path_index]
        anchors += [path_index[e["to"]] for e in down if e["from"] == box.id and e["to"] in path_index]
        want = path[min(anchors)].x if anchors else next_x
        box.x = max(want, next_x)
        adjacent = any(box.id in (e["to"], e["from"]) for e in side_inline)
        next_x = box.x + box_w + (gap if adjacent else side_gap)

    arcs_top.sort(key=lambda e: abs(path_index[e["to"]] - path_index[e["from"]]))
    arcs_bottom.sort(key=lambda e: abs(side_index[e["to"]] - side_index[e["from"]]))
    top_rows = len(arcs_top) + 1 if arcs_top else 0
    path_y = top_rows
    connectors = len(down)
    mid_gap = max(connectors + 2, 3) if sides else 0
    side_y = path_y + path_h + mid_gap
    bottom_rows = len(arcs_bottom) + 1 if arcs_bottom else 0
    ticker_free = side_y + side_h if sides else path_y + path_h
    total_h = ticker_free + bottom_rows
    for box in path:
        box.y = path_y
    for box in sides:
        box.y = side_y
    total_w = max((b.x + b.w for b in boxes), default=0) + MARGIN

    canvas = Canvas(total_w, max(total_h, 1), ascii_only)
    routes: dict[str, Route] = {}
    token_at: dict[str, tuple[int, int, int]] = {}
    h_line, h_dash = ("-", "-") if ascii_only else ("─", "┄")
    v_line, v_dash = ("|", "|") if ascii_only else ("│", "┆")

    def edge_id(edge: Json) -> str:
        return edge.get("id") or f"{edge['from']}->{edge['to']}"

    def edge_style(edge: Json) -> str:
        if edge_id(edge) in motion.lit:
            return "bold bright_magenta"
        return "" if _is_nominal(edge) else "dim"

    def stroke(edge: Json) -> tuple[str, str]:
        return (h_line, v_line) if _is_nominal(edge) else (h_dash, v_dash)

    # Boxes first, arrows over their stubs.
    for box in boxes:
        _draw_box(canvas, box, cards, motion, collapsed, ascii_only, token_at)

    ports = _Ports()
    labels: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def defer(*args: Any, **kw: Any) -> None:
        labels.append((args, kw))

    def record(edge: Json, cells: list[tuple[int, int]]) -> None:
        routes[edge_id(edge)] = Route(edge_id(edge), edge["from"], edge["to"], cells, _is_nominal(edge))

    arrow = ">" if ascii_only else "▶"
    up_arrow, down_arrow = ("^", "v") if ascii_only else ("▲", "▼")

    # Between neighbours on the path.
    for edge in inline:
        a, b = by_id[edge["from"]], by_id[edge["to"]]
        y = a.y + 1
        xs = range(a.x + a.w, b.x)
        style = edge_style(edge)
        char, _ = stroke(edge)
        cells = []
        for x in xs:
            canvas.put(x, y, char, style)
            cells.append((x, y))
        canvas.put(b.x - 1, y, arrow, style or "bold")
        record(edge, cells)
    for edge in side_inline:
        a, b = by_id[edge["from"]], by_id[edge["to"]]
        y = a.y + 1
        style = edge_style(edge)
        char, _ = stroke(edge)
        cells = []
        for x in range(a.x + a.w, b.x):
            canvas.put(x, y, char, style)
            cells.append((x, y))
        canvas.put(b.x - 1, y, arrow, style or "bold")
        record(edge, cells)
        defer(a.x + a.w + 1, y - 1, how_reached(edge), b.x - a.x - a.w - 1)

    # Over the row (a rejection goes back up the path) and under the side row.
    def arc(edge: Json, rank: int, *, above: bool) -> None:
        a, b = by_id[edge["from"]], by_id[edge["to"]]
        xs = ports.take(a, "top" if above else "bottom")
        xt = ports.take(b, "top" if above else "bottom")
        y0 = a.y - 1 if above else a.y + a.h
        rail = (path_y - 2 - rank) if above else (ticker_free + 1 + rank)
        style = edge_style(edge)
        h, v = h_line, v_line  # an arc is one unbroken line, whatever the edge's kind
        cells: list[tuple[int, int]] = []
        step = -1 if above else 1
        for y in range(y0, rail + step, step):
            canvas.put(xs, y, v, style, cross=True)
            cells.append((xs, y))
        lo, hi = sorted((xs, xt))
        horizontal = range(xs + (1 if xt > xs else -1), xt, 1 if xt > xs else -1)
        for x in horizontal:
            canvas.put(x, rail, h, style, cross=True)
            cells.append((x, rail))
        for y in range(rail, (b.y - 1 if above else b.y + b.h) + (-step), -step):
            canvas.put(xt, y, v, style, cross=True)
            cells.append((xt, y))
        end_y = b.y - 1 if above else b.y + b.h
        canvas.put(xt, end_y, down_arrow if above else up_arrow, style or "bold")
        if not ascii_only:
            _corner(canvas, xs, rail, xt, above, style)
        record(edge, cells)
        defer(lo + 2, rail, how_reached(edge), hi - lo - 3, on_line=True)

    for rank, edge in enumerate(arcs_top):
        arc(edge, rank, above=True)
    for rank, edge in enumerate(arcs_bottom):
        arc(edge, rank, above=False)

    # Between the path and the side states.
    for k, edge in enumerate(down):
        a, b = by_id[edge["from"]], by_id[edge["to"]]
        outward = a.id in path_index
        upper, lower = (a, b) if outward else (b, a)
        xu = ports.take(upper, "bottom")
        xl = ports.take(lower, "top")
        bus = path_y + path_h + 1 + k
        style = edge_style(edge)
        h, v = stroke(edge)
        cells = []
        sequence: list[tuple[int, int]] = [(xu, y) for y in range(path_y + path_h, bus + 1)]
        step = 1 if xl > xu else -1
        sequence += [(x, bus) for x in range(xu + step, xl, step)] if xl != xu else []
        sequence += [(xl, y) for y in range(bus if xl != xu else bus + 1, side_y)]
        for x, y in sequence:
            horizontal_cell = y == bus and xl != xu and x not in (xu, xl)
            canvas.put(x, y, h if horizontal_cell else v, style, cross=True)
        if xl != xu:
            _bend(canvas, xu, bus, xl, style, ascii_only)
        tip_x, tip_y = (xl, side_y - 1) if outward else (xu, path_y + path_h)
        canvas.put(tip_x, tip_y, down_arrow if outward else up_arrow, style or "bold")
        cells = sequence if outward else list(reversed(sequence))
        record(edge, cells)
        run = abs(xl - xu) - 1
        text = how_reached(edge)
        if text and xl != xu and run >= len(text) + 2:
            defer(min(xu, xl) + 1 + (run - len(text)) // 2, bus, text, len(text), on_line=True)
        elif text:
            defer(max(xu, xl) + 2, bus, text, 28)

    # Labels last: each takes only cells that are still blank, or its own arrow's.
    for args, kw in labels:
        _label(canvas, *args, **kw)

    # What moves: the lit arrows were drawn above; the tokens on their arrows are drawn last.
    for card_id, (edge_key, progress) in motion.travel.items():
        route = routes.get(edge_key)
        card = cards.get(card_id)
        if route is None or card is None or progress >= 1 or not route.cells:
            continue
        index = min(len(route.cells) - 1, int(progress * len(route.cells)))
        x, y = route.cells[index]
        dot = "*" if ascii_only else "●"
        canvas.put(x, y, dot, "bold bright_magenta")
        if index > 0:
            px, py = route.cells[index - 1]
            canvas.put(px, py, "." if ascii_only else "•", "bright_magenta")
        key = str(card["key"])
        if canvas.blank(x + 2, y, len(key)) and (x + 2 + len(key) < total_w):
            canvas.text(x + 2, y, key, "bold bright_magenta")

    order = [cid for box in boxes for cid in box.cards]
    return Drawing(canvas.lines(), total_w, canvas.height, token_at, routes, order, collapsed, by_id)


def _corner(canvas: Canvas, xs: int, rail: int, xt: int, above: bool, style: str) -> None:
    """Round the two corners of an arc's rail."""
    if above:
        left, right = "╭", "╮"
    else:
        left, right = "╰", "╯"
    canvas.put(xs, rail, right if xt < xs else left, style)
    canvas.put(xt, rail, left if xt < xs else right, style)


def _bend(canvas: Canvas, xu: int, bus: int, xl: int, style: str, ascii_only: bool) -> None:
    if ascii_only:
        return
    canvas.put(xu, bus, "╰" if xl > xu else "╯", style)
    canvas.put(xl, bus, "╮" if xl > xu else "╭", style)


def _label(canvas: Canvas, x: int, y: int, text: str, room: int, *, on_line: bool = False) -> None:
    """A label on its arrow, where there is room: over a line it takes its cells, beside one only blanks
    (to its right, else to its left)."""
    if not text or room < 4:
        return
    text = _fit(text, room)
    if on_line:
        canvas.text(x - 1, y, f" {text} ", "dim")
        return
    for start in (x, x - len(text) - 4):
        if start >= 0 and canvas.blank(start - 1, y, len(text) + 2):
            canvas.text(start, y, text, "dim")
            return


def _spinner(frame: int | None, ascii_only: bool) -> str:
    if frame is None:
        return "*" if ascii_only else "●"
    frames = SPINNER_ASCII if ascii_only else SPINNER
    return frames[frame % len(frames)]


def token_row(
    card: Json,
    box: Box,
    motion: Motion,
    ascii_only: bool,
    width: int,
    *,
    arriving: bool,
) -> list[tuple[str, str]]:
    """One token as `(text, style)` pieces, `width` cells wide: glyph, key, and on the right the word that
    says what it is doing (`2:14` for a run, the reason for a failure, what it waits for)."""
    status = card["status"]
    key = str(card["key"])
    if status == "running":
        glyph, glyph_style, key_style = _spinner(motion.frame, ascii_only), "bold blue", "bold"
        suffix, suffix_style = mmss((card["running"] or {}).get("elapsed_ms") or 0), "blue"
    elif status == "failed":
        glyph, glyph_style, key_style = ("x" if ascii_only else "✗"), "bold red", "red"
        suffix, suffix_style = reason_word((card["failure"] or {}).get("reason")), "red"
    elif status == "waiting":
        glyph, glyph_style, key_style = ("!" if ascii_only else "⏸"), "yellow", "yellow"
        suffix, suffix_style = str((card["waiting"] or {}).get("kind") or "waits").replace("_", " "), "yellow"
    elif box.lane_kind == "terminal":
        glyph, glyph_style, key_style = ("+" if ascii_only else "✓"), "green", "green"
        suffix, suffix_style = "", ""
    elif box.lane_kind in ("declined", "cancelled"):
        glyph, glyph_style, key_style = ("#" if ascii_only else "■"), "dim", "dim"
        suffix, suffix_style = "", ""
    else:
        glyph, glyph_style, key_style = ("o" if ascii_only else "○"), "dim", "dim"
        suffix, suffix_style = "", ""
    if arriving:
        glyph, glyph_style, key_style = " ", "", "dim"
    elif card["id"] in motion.fresh:
        key_style = f"{key_style} reverse".replace("dim ", "")
    room = width - 2 - (len(suffix) + 1 if suffix else 0)
    key = _fit(key, max(4, room))
    pad = max(1, width - 2 - len(key) - len(suffix))
    return [(glyph, glyph_style), (" ", ""), (key, key_style), (" " * pad, ""), (suffix, suffix_style)]


def _draw_box(  # noqa: C901
    canvas: Canvas,
    box: Box,
    cards: dict[str, Json],
    motion: Motion,
    collapsed: bool,
    ascii_only: bool,
    token_at: dict[str, tuple[int, int, int]],
) -> None:
    style, border = _border_style(box, cards)
    if any(cid in motion.fresh and motion.fresh[cid] > 0 for cid in box.cards) and "bold" not in style:
        style = f"bold {style}".strip()
    tl, tr, bl, br, h, v = tuple(BOX["ascii" if ascii_only else border])
    x, y, w, hgt = box.x, box.y, box.w, box.h
    inner = w - 2
    canvas.put(x, y, tl, style)
    canvas.put(x + w - 1, y, tr, style)
    canvas.put(x, y + hgt - 1, bl, style)
    canvas.put(x + w - 1, y + hgt - 1, br, style)
    for dx in range(1, w - 1):
        canvas.put(x + dx, y, h, style)
        canvas.put(x + dx, y + hgt - 1, h, style)
    for dy in range(1, hgt - 1):
        canvas.put(x, y + dy, v, style)
        canvas.put(x + w - 1, y + dy, v, style)
    count = str(box.count)
    title_style = "bold" + (" " + KIND_STYLE.get(box.kind, "") if box.kind == "agent" else "")
    title = _fit(box.display, inner - 2 - len(count) - 2)
    canvas.text(x + 2, y + 1, title, title_style.strip())
    canvas.text(x + w - 2 - len(count), y + 1, count, "bold" if box.count else "dim")
    if collapsed:
        statuses = [cards[c]["status"] for c in box.cards]
        marks = []
        for status, glyph, mark_style in (
            ("running", "*" if ascii_only else "●", "blue"),
            ("waiting", "!" if ascii_only else "⏸", "yellow"),
            ("failed", "x" if ascii_only else "✗", "red"),
        ):
            n = statuses.count(status)
            if n:
                marks.append((f"{glyph}{n}", mark_style))
        if box.lane_kind in ("declined", "cancelled") or box.terminal:
            rest = statuses.count("idle")
            if rest:
                done = box.lane_kind not in ("declined", "cancelled")
                glyph = ("+" if ascii_only else "✓") if done else ("#" if ascii_only else "■")
                marks.append((f"{glyph}{rest}", "green" if done else "dim"))
        elif statuses.count("idle"):
            marks.append((f"{'o' if ascii_only else '○'}{statuses.count('idle')}", "dim"))
        cursor = x + 2
        for text, mark_style in marks:
            canvas.text(cursor, y + 2, text, mark_style)
            cursor += len(text) + 1
        if not marks:
            canvas.text(x + 2, y + 2, _fit(box.who, inner - 1), "dim")
        picked = next((c for c in box.cards if c == motion.selected), None)
        if picked:
            key = str(cards[picked]["key"])
            canvas.text(x + 1, y + 3, "›", "bold")
            canvas.text(x + 2, y + 3, _fit(key, inner - 2), "bold")
            token_at[picked] = (x + 2, y + 3, len(key))
        return
    canvas.text(x + 2, y + 2, _fit(box.who, inner - 1), "dim")
    shown, more = _tokens_of(box, cards, motion.selected, motion.fresh)
    travelling = {cid for cid, (_, progress) in motion.travel.items() if progress < 1}
    for row, cid in enumerate(shown):
        ty = y + 3 + row
        pieces = token_row(cards[cid], box, motion, ascii_only, inner - 2, arriving=cid in travelling)
        cursor = x + 2
        for text, piece_style in pieces:
            canvas.text(cursor, ty, text, piece_style)
            cursor += len(text)
        if cid == motion.selected:
            canvas.put(x + 1, ty, ">" if ascii_only else "›", "bold")
        token_at[cid] = (x + 2, ty, inner - 2)
    if more:
        canvas.text(x + 2, y + 3 + len(shown), f"+{more} more", "dim")
    elif not shown:
        canvas.text(x + 2, y + 3, "nothing here", "dim")


# ──────────────────────────────── header, summary, ticker ────────────────────────────────

_TICKER = {
    "started": (">", "▶", "blue"),
    "succeeded": ("+", "✓", "green"),
    "failed": ("x", "✗", "red"),
    "cancelled": ("#", "■", "dim"),
    "moved": ("-", "→", "magenta"),
    "decided": ("+", "✓", "yellow"),
}


def money(usd: float | None, eur: float | None, currency: str) -> str:
    """An amount in the organisation's currency, with its symbol; unknown is `—`, never 0 (ADR 0141 §2).

    The cost report carries both totals, so a euro organisation is never shown dollars relabelled. When
    S67-25's `money.py` lands this is its one call.
    """
    value = eur if currency == "EUR" else usd
    if value is None:
        return "—"
    return f"€{value:.2f}" if currency == "EUR" else f"${value:.2f}"


def local_time(iso: str | None) -> str:
    if not iso:
        return "--:--:--"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")


def header(
    project: str,
    workflow: str,
    connection: str,
    now_iso: str,
    width: int,
    ascii_only: bool,
    hue_style: str = "",
) -> Text:
    """`Billing API · default-simple`, then `LIVE ●` (or `RECONNECTING`) and the clock, right-aligned."""
    live = connection == "live"
    left = Text()
    left.append(project, style="bold")
    left.append(" · ", style="dim")
    left.append(workflow, style=f"bold {hue_style}".strip())
    dot = "*" if ascii_only else ("●" if live else "◌")
    right = Text()
    word = f"LIVE {dot}" if live else f"RECONNECTING {dot}"
    right.append(word, style="bold green" if live else "bold yellow")
    right.append("   ")
    right.append(local_time(now_iso), style="dim")
    pad = max(2, width - left.cell_len - right.cell_len - 2)
    line = Text(no_wrap=True, overflow="crop")
    line.append(" ")
    line.append_text(left)
    line.append(" " * pad)
    line.append_text(right)
    return line


def summary(view: Json, spend: str | None, width: int) -> Text:
    """`1 in flight · 2 waiting for a person · 1 failed · 1 done today · €2.12 spent today`."""
    count = {s: sum(1 for c in view["cards"] if c["status"] == s) for s in ("running", "waiting", "failed")}
    done = sum(lane["closed_today"] for lane in view["lanes"])
    line = Text(no_wrap=True, overflow="crop")
    line.append(" ")
    parts: list[tuple[str, str, str]] = [
        (str(count["running"]), "in flight", "bold blue" if count["running"] else "dim"),
        (str(count["waiting"]), "waiting for a person", "bold yellow" if count["waiting"] else "dim"),
        (str(count["failed"]), "failed", "bold red" if count["failed"] else "dim"),
        (str(done), "done today", "bold green" if done else "dim"),
    ]
    parts.append((spend if spend is not None else "—", "spent today", "bold" if spend else "dim"))
    for index, (figure, label, style) in enumerate(parts):
        if index:
            line.append("  ·  ", style="dim")
        line.append(figure, style=style)
        line.append(f" {label}")
    return line


def _instant(iso: str | None) -> datetime:
    if not iso:
        return datetime.min.replace(tzinfo=UTC)
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def ticker_lines(view: Json, rows: int, width: int, ascii_only: bool) -> list[Text]:
    """The last events, newest first: a run started, ended, failed; an item moved; a decision taken."""
    display = {lane["state"]: lane["display"] for lane in view["lanes"]}
    out: list[Text] = []
    newest = sorted(view["ticker"], key=lambda e: _instant(e["at"]), reverse=True)
    for entry in newest[:rows]:
        plain, fancy, style = _TICKER.get(entry["kind"], ("-", "·", ""))
        line = Text(no_wrap=True, overflow="crop")
        line.append(f" {local_time(entry['at'])}  ", style="dim")
        line.append(plain if ascii_only else fancy, style=style)
        line.append(f" {entry['key']}", style="bold")
        line.append("  ")
        stage = entry.get("stage")
        kind = entry["kind"]
        if kind == "moved":
            arrow = ">" if ascii_only else "→"
            origin = display.get(entry["from"], entry["from"])
            target = display.get(entry["to"], entry["to"])
            line.append(f"{origin} {arrow} {target}")
        elif kind == "failed":
            line.append(f"failed {stage or ''}".rstrip())
            line.append(f" ({reason_long(entry.get('reason'))})", style="red")
        elif kind == "decided":
            line.append(f"decision taken ({str(entry.get('reason') or '').replace('_', ' ')})")
        else:
            verb = {"started": "started", "succeeded": "finished", "cancelled": "cancelled"}[kind]
            line.append(f"{verb} {stage or ''}".rstrip())
        out.append(line)
    if not out:
        out.append(Text(" nothing has happened yet", style="dim"))
    return out
