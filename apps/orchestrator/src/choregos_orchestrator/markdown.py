# SPDX-License-Identifier: Apache-2.0
"""Rendering of the tracking comment written in the ticket (docs/plan/04 §4.3).

A single comment, found by a marker and rewritten at each stage: it is the human view of the
platform's work (stages, actors, tokens, costs, durations, result). In English (ADR 0039,
S21-13): the tracker shows it; the markers do not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

STATUS_MARKER = "<!-- choregos:status -->"
TABLE_HEADER = "| # | Step | Actor | Backend · model | Tokens in / out (cached) | Cost | Duration | Result |"


@dataclass(slots=True)
class StageLine:
    """One row of the tracking table."""

    index: int
    label: str
    actor: str
    backend: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_eur: float = 0.0
    duration_s: float = 0.0
    outcome: str = ""

    def render(self) -> str:
        engine = f"{self.backend} · {self.model}" if self.backend else "—"
        if self.tokens_in or self.tokens_out:
            cached = f" ({_thousands(self.tokens_cached)})" if self.tokens_cached else ""
            tokens = f"{_thousands(self.tokens_in)} / {_thousands(self.tokens_out)}{cached}"
            cost = _eur(self.cost_eur)
        else:
            tokens = "—"
            cost = "—"
        duration = _duration(self.duration_s) if self.duration_s else "—"
        cells = [str(self.index), self.label, self.actor, engine, tokens, cost, duration, self.outcome]
        return "| " + " | ".join(cells) + " |"


@dataclass(slots=True)
class StatusComment:
    """The whole comment: header, stages table, total, findings, links."""

    workflow: str
    workflow_version: int
    size: str | None
    risk: str | None
    state_display: str
    budget_eur: float
    spent_eur: float
    pr_url: str | None = None
    run_url: str | None = None
    estimate_eur: float | None = None
    over_estimate: bool = False
    findings: list[str] = field(default_factory=list)
    memory_note: str | None = None
    lines: list[StageLine] = field(default_factory=list)

    def render(self) -> str:
        header = (
            f"**Workflow** {self.workflow} v{self.workflow_version} · "
            f"**Size** {self.size or '—'} · **Risk** {self.risk or '—'} · "
            f"**State** {self.state_display}"
        )
        if self.pr_url:
            header += f" · **PR** {self.pr_url}"
        header += f" · **Budget** {_eur(self.spent_eur)} / {_eur(self.budget_eur)}"

        rows = [line.render() for line in self.lines]
        totals = _totals(self.lines)
        estimate = ""
        if self.estimate_eur is not None:
            estimate = f"estimated {_eur(self.estimate_eur)}" + (
                " ⚠ over the p80" if self.over_estimate else ""
            )
        rows.append(
            f"| | **Total** | | | **{_thousands(totals[0])} / {_thousands(totals[1])}** | "
            f"**{_eur(totals[2])}** | {_duration(totals[3])} | {estimate} |"
        )

        footer_parts: list[str] = []
        if self.findings:
            footer_parts.append("Findings filed: " + " · ".join(self.findings))
        if self.run_url:
            footer_parts.append(f"Run: {self.run_url}")
        if self.memory_note:
            footer_parts.append(self.memory_note)

        return (
            "\n".join(
                [
                    STATUS_MARKER,
                    "### Choregos — progress",
                    header,
                    "",
                    TABLE_HEADER,
                    "|--:|:--|:--|:--|--:|--:|--:|:--|",
                    *rows,
                    "",
                    " · ".join(footer_parts) if footer_parts else "",
                ]
            ).rstrip()
            + "\n"
        )


def _totals(lines: list[StageLine]) -> tuple[int, int, float, float]:
    return (
        sum(line.tokens_in for line in lines),
        sum(line.tokens_out for line in lines),
        sum(line.cost_eur for line in lines),
        sum(line.duration_s for line in lines),
    )


def _thousands(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} M"
    if value >= 1000:
        return f"{round(value / 1000)} k"
    return str(value)


def _eur(value: float) -> str:
    return f"€{value:.2f}"


def _duration(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    days, remainder = divmod(int(delta.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{secs} s"


def render_human_request(kind: str, payload: dict[str, object], public_url: str, key: str) -> str:
    """Comment posted when the platform asks for a human decision.

    It points to the INTERFACE, the only path that settles a request. It used to invite a reply
    with `/choregos approve`: those comments are parsed but the workflow translates them into
    nothing (#138), so the human replied and the ticket stayed blocked with no sign.
    """
    if kind == "question":
        options = payload.get("options") or []
        lines = [
            "<!-- choregos:human -->",
            "### Choregos — question",
            str(payload.get("question", "")),
        ]
        if isinstance(options, list) and options:
            lines += ["", *[f"- {option}" for option in options]]
        lines += [
            "",
            f"Answer in the console: {public_url}",
        ]
        return "\n".join(lines)
    if kind == "scope_change":
        paths = payload.get("paths") or []
        return "\n".join(
            [
                "<!-- choregos:human -->",
                "### Choregos — wider scope requested",
                f"Reason: {payload.get('justification', '')}",
                "",
                "Paths requested:",
                *[f"- `{path}`" for path in (paths if isinstance(paths, list) else [])],
                "",
                f"Grant or refuse in the console: {public_url}",
            ]
        )
    return "\n".join(
        [
            "<!-- choregos:human -->",
            "### Choregos — approval requested",
            str(payload.get("summary", "A person's decision is needed to continue.")),
            "",
            f"Approve or refuse in the console: {public_url}",
        ]
    )
