# SPDX-License-Identifier: Apache-2.0
"""`choregos activity --run ID` (S67-03, ADR 0154 §2-3, §9): what one run did, read from its ACP journal.

The plan the agent declared, its tool calls (state, elapsed, title) and the latest breakdown of what fills the
model's context, in the frame of `choregos activity`. Reads `GET /runs/{id}`, `GET /runs/{id}/events`
(`after_seq`) and `GET /runs/{id}/context-breakdown`, and nothing else. Read-only.

`--watch` follows it live by asking for what is after the last event number read, every `POLL_EVERY_S`, until
the run has ended: the journal's SSE carries the stored events once and then the bus's progress notices, not
the ACP updates themselves, so polling `after_seq` is the one way to read each update exactly once.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from . import theme
from .activity import DASH, IN_FLIGHT, clock, elapsed
from .client import ApiError, Client
from .run_steps import RunSteps, Step, Task

#: How often `--watch` asks for what is new.
POLL_EVERY_S = 2.0
#: The latest steps shown; earlier ones are counted ("… 12 earlier").
STEPS_SHOWN = 40
BAR = 20


def show(api: Client, run_id: str, *, watch: bool, out: Console) -> None:
    """Print the run's steps once, or keep them live until the run ends or the person interrupts."""
    steps = RunSteps()
    run = api.get(f"/runs/{run_id}")
    _read(api, run_id, steps)
    breakdown = _breakdown(api, run_id)
    if not watch:
        out.print(render(run, steps, breakdown, out))
        return
    try:
        with Live(render(run, steps, breakdown, out), console=out, auto_refresh=False) as live:
            while run.get("status") in IN_FLIGHT:
                time.sleep(POLL_EVERY_S)
                run = api.get(f"/runs/{run_id}")
                _read(api, run_id, steps)
                breakdown = _breakdown(api, run_id)
                live.update(render(run, steps, breakdown, out), refresh=True)
    except KeyboardInterrupt:
        return


def _read(api: Client, run_id: str, steps: RunSteps) -> None:
    steps.apply(api.get(f"/runs/{run_id}/events", params={"after_seq": steps.last_seq}) or [])


def _breakdown(api: Client, run_id: str) -> dict[str, Any] | None:
    """The latest breakdown, or `None` when the run has none (a backend the platform cannot see into, or no
    call measured yet)."""
    try:
        context = api.get(f"/runs/{run_id}/context-breakdown") or {}
    except ApiError as exc:
        if exc.status_code == 404:
            return None
        raise
    return latest_of(context)


def latest_of(context: dict[str, Any]) -> dict[str, Any] | None:
    """The latest breakdown of a `RunContext`, or `None` when the backend is not measurable."""
    latest = context.get("latest")
    return latest if context.get("measurable") and isinstance(latest, dict) else None


def render(
    run: dict[str, Any], steps: RunSteps, breakdown: dict[str, Any] | None, out: Console
) -> RenderableType:
    parts: list[RenderableType] = [
        _header(run, out),
        _plan(steps, out),
        _steps(steps, out),
        _context(breakdown, out),
    ]
    return Panel(
        Group(*parts),
        title=Text(f"Run {str(run.get('id'))[:12]}", style="bold"),
        title_align="left",
        border_style=theme.TREE_FRAME,
    )


def _header(run: dict[str, Any], out: Console) -> Text:
    text = theme.badge(theme.status_of(str(run.get("status") or "")), out)
    what = [run.get("stage_role"), run.get("agent_slug"), run.get("backend"), run.get("model")]
    text.append("  " + " · ".join(str(w).split("/")[-1] for w in what if w))
    return text


def _plan(steps: RunSteps, out: Console) -> RenderableType:
    if steps.plan is None:
        return Text("\nPlan: none declared", style="bright_black")
    done = sum(1 for task in steps.plan if task.status == "completed")
    lines = [Text(f"\nPlan  {done}/{len(steps.plan)} done", style="bold")]
    lines.extend(task_line(task, out, indent="  ") for task in steps.plan)
    return Group(*lines)


def task_line(task: Task, out: Console, *, indent: str = "") -> Text:
    """`✓ Read the totals code`: one task of the plan."""
    status = theme.status_of(task.status)
    line = Text(f"{indent}{theme.glyph(status, out)} ", style=status.style)
    line.append(task.content)
    line.no_wrap, line.overflow = True, "ellipsis"
    return line


def _steps(steps: RunSteps, out: Console) -> RenderableType:
    now = clock()
    shown = list(steps.steps.values())[-STEPS_SHOWN:]
    header = f"\nSteps  {len(steps.steps)} tool calls · {steps.finished()} finished"
    if steps.unknown:
        header += f" · {steps.unknown} update{'s' if steps.unknown > 1 else ''} of an unknown kind skipped"
    lines: list[Text] = [Text(header, style="bold")]
    if len(steps.steps) > len(shown):
        lines.append(Text(f"  … {len(steps.steps) - len(shown)} earlier", style="bright_black"))
    lines.extend(step_line(step, now, out, indent="  ") for step in shown)
    if not steps.steps:
        lines.append(Text("  no tool call yet", style="bright_black"))
    return Group(*lines)


def step_line(step: Step, now: datetime, out: Console, *, indent: str = "") -> Text:
    """`✓ done          2s  read     Read src/invoices/total.py`: one tool call, its state, its time."""
    status = theme.status_of(step.status)
    line = Text(f"{indent}{theme.glyph(status, out)} ", style=status.style)
    took = elapsed({"started_at": step.started_at or None, "ended_at": step.ended_at}, now)
    line.append(f"{status.word:<11} {took:>6}  ")
    line.append(f"{step.kind:<8} {step.title}")
    # One line per step: on a narrow terminal the title is cut, never the state or the time.
    line.no_wrap, line.overflow = True, "ellipsis"
    return line


def tokens(value: int) -> str:
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def context_head(breakdown: dict[str, Any]) -> str:
    """`Context  call 7 · sonnet-4.5 · 40.0k of 200.0k (20%)`: what the model's context holds now."""
    total = int(breakdown.get("prompt_tokens") or 0)
    window = breakdown.get("max_input_tokens")
    model = str(breakdown.get("model") or DASH).split("/")[-1]
    head = f"Context  call {breakdown.get('call')} · {model} · {tokens(total)}"
    if isinstance(window, int) and window > 0:
        head += f" of {tokens(window)} ({total * 100 // window}%)"
    return head


def _context(breakdown: dict[str, Any] | None, out: Console) -> RenderableType:
    if breakdown is None:
        return Text("\nContext: not measured", style="bright_black")
    total = int(breakdown.get("prompt_tokens") or 0)
    lines: list[Text] = [Text("\n" + context_head(breakdown), style="bold")]
    full, empty = ("█", "░") if theme.unicode_ok(out) else ("#", "-")
    sections = sorted(
        ((k, int(v or 0)) for k, v in (breakdown.get("sections") or {}).items()), key=lambda kv: -kv[1]
    )
    for name, count in sections:
        share = count / total if total else 0.0
        filled = round(share * BAR)
        lines.append(
            Text(f"  {name:<15} {tokens(count):>7} {full * filled}{empty * (BAR - filled)} {share:.0%}")
        )
    return Group(*lines)
