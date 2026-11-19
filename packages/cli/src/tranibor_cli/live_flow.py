# SPDX-License-Identifier: Apache-2.0
"""The live flow's reduction (S67-26, ADR 0187 §4): (graph, items, stream events) to (lanes, cards, ticker).

Pure: no clock, no network, no Textual, no Rich. `apps/web/src/lib/live-flow.ts` is the same function written
in TypeScript, and both are tested against the fixtures of `packages/contracts/fixtures/live/`, so the web
and the terminal cannot show a different flow for the same events. A change here is a change there, and a
fixture that shows it.

The lanes are the board's columns (`apps/web/src/components/workflows/board.ts`, `columnsFromGraph`): the
workflow's states in the validator's order, then a lane of its own for a state the workflow does not know.
The inputs are the API's JSON (a `WorkflowGraph`, `WorkItem`s, `ActivityEntry`s as dicts); the output keys are
the fixtures' keys.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

DEFAULT_TICKER_SIZE = 12
#: A failure's summary is cut to this many characters, as the API cuts it (ADR 0187 §3).
SUMMARY_MAX = 300
#: What a card says it waits for stays on one line.
WAITING_LABEL_MAX = 80

IN_FLIGHT = frozenset({"queued", "running"})
FAILED = frozenset({"failed", "timed_out"})

Json = dict[str, Any]


def _ms(iso: str) -> int:
    """An ISO instant as epoch milliseconds (a trailing `Z` is UTC)."""
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def cut(text: str | None, limit: int) -> str:
    """One line, whitespace collapsed, cut to `limit` characters with an ellipsis."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _first_line(text: str) -> str:
    return next((line for line in text.split("\n") if line.strip()), "")


def _waiting_of(item: Json) -> Json | None:
    request = item.get("pending_request")
    if not request or request.get("decided_at"):
        return None
    payload = request.get("payload") or {}
    text = next(
        (
            payload[k]
            for k in ("title", "question", "summary")
            if isinstance(payload.get(k), str) and payload[k].strip()
        ),
        request["kind"],
    )
    return {"kind": request["kind"], "label": cut(_first_line(text), WAITING_LABEL_MAX)}


def _item_failure_of(item: Json) -> Json | None:
    failure = item.get("failure")
    if failure:
        return {"reason": "workflow", "summary": cut(failure.get("message"), SUMMARY_MAX)}
    run = item.get("current_run")
    if run and run["status"] in FAILED:
        return {"reason": run["status"], "summary": ""}
    return None


def _run_of_item(item: Json) -> Json | None:
    run = item.get("current_run")
    if not run or run["status"] not in IN_FLIGHT:
        return None
    return {
        "run_id": run["id"],
        "stage": run["stage_role"],
        "agent": None,
        "started_at": run.get("started_at"),
        "cost_usd": run.get("cost_usd"),
    }


def _slot_of(item: Json) -> Json:
    return {
        "id": item["id"],
        "key": item["tracker_key"],
        "title": item["title"],
        "lane": item["state"],
        "lane_display": item.get("state_display"),
        "run": _run_of_item(item),
        "stream_failure": None,
        "item_failure": _item_failure_of(item),
        "waiting": _waiting_of(item),
        "last_move": None,
    }


def edge_between(graph: Json | None, source: str, target: str) -> str | None:
    """The id of the edge from one state to another (`from->to` without one), None when there is none."""
    for edge in (graph or {}).get("edges", []):
        if edge["from"] == source and edge["to"] == target:
            return edge.get("id") or f"{source}->{target}"
    return None


def lanes_from_graph(graph: Json | None, items: list[Json]) -> list[Json]:
    """The board's `columnsFromGraph`: the graph's nodes in order, then the states the graph does not know.

    `items` are only read for their `state` and `state_display`. A lane is
    `{state, display, kind, terminal, hors_workflow}` (counts and cards are added by `view`).
    """
    lanes: list[Json] = []
    for node in (graph or {}).get("nodes", []):
        if node.get("terminal"):
            outcome = node.get("outcome")
            kind: str | None = outcome if outcome in ("declined", "cancelled") else "terminal"
        else:
            kind = "wait" if node.get("kind") == "wait" else None
        lanes.append(
            {
                "state": node["id"],
                "display": node["display"],
                "kind": kind,
                "terminal": bool(node.get("terminal")),
                "hors_workflow": False,
            }
        )
    known = {lane["state"] for lane in lanes}
    first: dict[str, Json] = {}
    for item in items:
        first.setdefault(item["state"], item)
    for state, item in first.items():
        if state not in known:
            lanes.append(
                {
                    "state": state,
                    "display": item.get("state_display") or state,
                    "kind": None,
                    "terminal": False,
                    "hors_workflow": True,
                }
            )
    return lanes


def initial_state(graph: Json | None, items: list[Json], options: Json | None = None) -> Json:
    """The state after a first read of the work items."""
    options = options or {}
    state: Json = {
        "graph": graph,
        "slots": [],
        "seen": {},
        "left": {},
        "closed": {},
        "ticker": [],
        "ticker_size": options.get("ticker_size") or DEFAULT_TICKER_SIZE,
        "day_start": options.get("day_start"),
    }
    for item in items:
        state["slots"].append(_slot_of(item))
        run = item.get("current_run")
        if run:
            state["seen"][run["id"]] = run["status"]
        closed_at = item.get("closed_at")
        if closed_at and _today(state, closed_at):
            state["closed"][item["state"]] = state["closed"].get(item["state"], 0) + 1
    return state


def _today(state: Json, iso: str | None) -> bool:
    start = state["day_start"]
    return bool(start and iso and _ms(iso) >= _ms(start))


def _push(state: Json, entry: Json) -> None:
    state["ticker"] = [entry, *state["ticker"]][: state["ticker_size"]]


def _ticker(at: str, kind: str, key: str, **fields: Any) -> Json:
    return {
        "at": at,
        "kind": kind,
        "key": key,
        "stage": fields.get("stage"),
        "from": fields.get("from_"),
        "to": fields.get("to"),
        "reason": fields.get("reason"),
    }


def apply_items(state: Json, items: list[Json], at: str) -> Json:
    """A fresh read of the work items: a card whose state changed moves to the end of its new lane.

    An item that appeared is added, one that vanished is dropped; `at` stamps a move in the ticker.
    """
    nxt = copy.deepcopy(state)
    known = {slot["id"]: slot for slot in state["slots"]}
    order = {slot["id"]: index for index, slot in enumerate(state["slots"])}
    stay: list[Json] = []
    moved: list[Json] = []
    added: list[Json] = []
    for item in items:
        before = known.get(item["id"])
        waiting = _waiting_of(item)
        snapshot_run = _run_of_item(item)
        current = item.get("current_run")
        ended = current["id"] if current and current["status"] not in IN_FLIGHT else None
        if current:
            seen = nxt["seen"].get(current["id"])
            if not seen or current["status"] in IN_FLIGHT:
                nxt["seen"][current["id"]] = current["status"]
        if before is None:
            added.append(_slot_of(item))
            continue
        slot = copy.deepcopy(before)
        slot.update(
            title=item["title"],
            key=item["tracker_key"],
            lane_display=item.get("state_display"),
            waiting=waiting,
            item_failure=_item_failure_of(item),
        )
        if snapshot_run and (slot["run"] or {}).get("run_id") != snapshot_run["run_id"]:
            slot["run"] = snapshot_run
            slot["stream_failure"] = None
        elif slot["run"] and ended == slot["run"]["run_id"]:
            slot["run"] = None
        if before["waiting"] and not waiting:
            slot["stream_failure"] = None
            _push(nxt, _ticker(at, "decided", slot["key"], reason=before["waiting"]["kind"]))
        if before["lane"] != item["state"]:
            _push(nxt, _ticker(at, "moved", slot["key"], from_=before["lane"], to=item["state"]))
            if _today(state, at):
                nxt["left"][before["lane"]] = nxt["left"].get(before["lane"], 0) + 1
            slot["lane"] = item["state"]
            slot["last_move"] = {
                "from": before["lane"],
                "to": item["state"],
                "edge": edge_between(state["graph"], before["lane"], item["state"]),
            }
            slot["stream_failure"] = None
            moved.append(slot)
            continue
        stay.append(slot)
    stay.sort(key=lambda slot: order[slot["id"]])
    nxt["slots"] = [*stay, *moved, *added]
    return nxt


def apply_event(state: Json, event: str, entry: Json) -> Json:
    """One `activity.upsert` or `activity.ended` message; anything else changes nothing."""
    if event not in ("activity.upsert", "activity.ended"):
        return state
    if entry.get("kind") != "run" or not entry.get("work_item_id"):
        return state
    index = next((i for i, s in enumerate(state["slots"]) if s["id"] == entry["work_item_id"]), -1)
    if index < 0:
        return state
    nxt = copy.deepcopy(state)
    slot = nxt["slots"][index]
    previous = state["seen"].get(entry["id"])
    stage = entry.get("stage_role")
    status = entry["status"]
    if status in IN_FLIGHT and event == "activity.upsert":
        nxt["seen"][entry["id"]] = status
        before_run = slot["run"] or {}
        spend = entry.get("spend")
        slot["run"] = {
            "run_id": entry["id"],
            "stage": stage or before_run.get("stage") or "run",
            "agent": entry.get("agent_slug") or before_run.get("agent"),
            "started_at": entry["started_at"],
            "cost_usd": spend["cost_usd"] if spend else before_run.get("cost_usd"),
        }
        slot["stream_failure"] = None
        if previous is None:
            _push(nxt, _ticker(entry["started_at"], "started", slot["key"], stage=stage))
        return nxt
    if status in IN_FLIGHT:
        return state
    if previous == status:
        return state
    nxt["seen"][entry["id"]] = status
    at = entry.get("ended_at") or entry["started_at"]
    failed = status in FAILED
    if slot["run"] and slot["run"]["run_id"] == entry["id"]:
        slot["run"] = None
    failure = entry.get("failure") or {}
    reason = failure.get("reason") or status
    if failed:
        slot["stream_failure"] = {"reason": reason, "summary": cut(failure.get("summary"), SUMMARY_MAX)}
    kind = "failed" if failed else "cancelled" if status == "cancelled" else "succeeded"
    _push(nxt, _ticker(at, kind, slot["key"], stage=stage, reason=reason if failed else None))
    return nxt


def view(state: Json, now: str) -> Json:
    """The view at `now` (ISO): lanes with their counts, cards (running ones timed), the ticker."""
    now_ms = _ms(now)
    cards: list[Json] = []
    for slot in state["slots"]:
        run = slot["run"]
        failure = None if run else (slot["stream_failure"] or slot["item_failure"])
        status = "running" if run else "failed" if failure else "waiting" if slot["waiting"] else "idle"
        running = None
        if run:
            started = run["started_at"]
            running = {
                "run_id": run["run_id"],
                "stage": run["stage"],
                "agent": run["agent"],
                "started_at": started,
                "elapsed_ms": max(0, now_ms - _ms(started)) if started else 0,
                "cost_usd": run["cost_usd"],
            }
        cards.append(
            {
                "id": slot["id"],
                "key": slot["key"],
                "title": slot["title"],
                "lane": slot["lane"],
                "status": status,
                "running": running,
                "failure": failure,
                "waiting": slot["waiting"],
                "transition": slot["last_move"],
            }
        )
    seeds = [{"state": s["lane"], "state_display": s["lane_display"]} for s in state["slots"]]
    lanes: list[Json] = []
    for lane in lanes_from_graph(state["graph"], seeds):
        in_lane = [card["id"] for card in cards if card["lane"] == lane["state"]]
        lanes.append(
            {
                **lane,
                "count": len(in_lane),
                "left_today": state["left"].get(lane["state"], 0),
                "closed_today": state["closed"].get(lane["state"], 0),
                "cards": in_lane,
            }
        )
    return {"lanes": lanes, "cards": cards, "ticker": state["ticker"]}


def reduce(
    graph: Json | None,
    items: list[Json],
    steps: list[Json],
    now: str,
    options: Json | None = None,
) -> Json:
    """One call for a fixture: the state after `items` and each of the `steps`, viewed at `now`."""
    state = initial_state(graph, items, options)
    for step in steps:
        if step.get("items") is not None:
            state = apply_items(state, step["items"], step.get("at") or now)
        elif step.get("event") and step.get("data"):
            state = apply_event(state, step["event"], step["data"])
    return view(state, now)
