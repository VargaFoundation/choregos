# SPDX-License-Identifier: Apache-2.0
"""What an agent touched, on one page.

WHY
---
Everything is already logged: every permission request, every tool called, every refusal and
its reason. But it is a raw ACP log, line by line: on a bench of two agents, 225 events in a few
minutes. Nobody reads that, so nobody knows what the agent touched. A log nobody reads is not
an audit.

This module **collects nothing new**. It folds the events already recorded into one report:
which files were read, which were written, which commands ran, which addresses were reached,
which catalogue tools were called, and what was REFUSED, with the reason.

WHAT IT DOES NOT DO
-------------------
It does not prove that an agent touched nothing else: the proof of isolation is the sandbox and
the network policy, not this report. It says what the agent ASKED for and what it was told. An
agent that writes outside its perimeter is refused here AND its diff is checked afterwards: two
mechanisms, not one.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: The access natures we can name. Anything else falls into `other` rather than being lost:
#: an access we cannot classify must stay visible.
NATURES = ("read", "write", "execute", "network", "tool", "other")


@dataclass(slots=True)
class AccessLine:
    """A target, and everything that happened to it."""

    nature: str
    target: str
    requests: int = 0
    refusals: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.refusals > 0

    # Former member names (ADR 0098 §3 (d)): read-only views of the renamed fields.

    def to_dict(self) -> dict[str, Any]:
        return {
            "nature": self.nature,
            "target": self.target,
            "requests": self.requests,
            "refusals": self.refusals,
            "reasons": self.reasons,
        }

    def former_dict(self) -> dict[str, Any]:
        """The line as it was served before S45-02: only `nature` and `motifs` differ."""
        return {
            "nature": "autre" if self.nature == "other" else self.nature,  # lang: fr-ok alias:external
            "target": self.target,
            "requests": self.requests,
            "refusals": self.refusals,
            "motifs": self.reasons,  # lang: fr-ok alias:external
        }


@dataclass(slots=True)
class AccessReport:
    lines: list[AccessLine] = field(default_factory=list)
    events: int = 0
    #: Decisions whose nature was INFERRED (the agent had not said `kind`). A report that says
    #: "0 refusals" reads differently when 23 natures were guessed.
    inferred: int = 0

    @property
    def refused_lines(self) -> list[AccessLine]:
        return [line for line in self.lines if line.refused]

    def by_nature(self, nature: str) -> list[AccessLine]:
        return [line for line in self.lines if line.nature == nature]

    # Former member names (ADR 0098 §3 (d)).

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "inferred": self.inferred,
            "refusals": sum(line.refusals for line in self.lines),
            "access": [line.to_dict() for line in self.lines],
            # Former names, emitted for one release (ADR 0046, S45-02).
            "evenements": self.events,  # lang: fr-ok alias:external
            "deduites": self.inferred,  # lang: fr-ok alias:external
            "acces": [line.former_dict() for line in self.lines],  # lang: fr-ok alias:external
        }


def _key(nature: str, target: str) -> tuple[str, str]:
    return (nature if nature in NATURES else "other", target)


def access_report(events: list[dict[str, Any]]) -> AccessReport:
    """Fold the log of a run into an access report.

    The events are the ones we already record: `session/request_permission` carries the
    guardrail's decision (nature, target, allowed, reason), and `choregos.tool.called` a call to
    the catalogue. The rest of the log, the agent's prose, does not enter here: this report
    answers "what did it touch", not "what did it say".
    """
    groups: OrderedDict[tuple[str, str], AccessLine] = OrderedDict()
    count = 0
    inferred = 0
    for event in events:
        count += 1
        type_ = str(event.get("type", ""))
        payload = event.get("payload") or {}
        if type_ == "session/request_permission":
            inferred += 1 if payload.get("inferred") else 0
            nature = str(payload.get("kind") or "other")
            target = str(payload.get("target") or "")
            if not target:
                continue
            allowed = bool(payload.get("allowed", True))
            reason = str(payload.get("reason") or "")
        elif type_ in {"choregos.tool.called", "tool.called"}:
            nature, target, allowed = "tool", str(payload.get("tool") or ""), True
            reason = str(payload.get("provider") or "")
            if not target:
                continue
        else:
            continue

        key = _key(nature, target)
        line = groups.setdefault(key, AccessLine(nature=key[0], target=target))
        line.requests += 1
        if not allowed:
            line.refusals += 1
            # The reason for a refusal is the most useful information of this whole report:
            # "write forbidden in a sensitive file" reads, "denied" does not.
            if reason and reason not in line.reasons:
                line.reasons.append(reason)

    # Refusals first: that is what one comes looking for. Then by number of requests.
    ordered = sorted(groups.values(), key=lambda a: (not a.refused, -a.requests, a.target))
    return AccessReport(lines=ordered, events=count, inferred=inferred)


__all__ = ["NATURES", "AccessLine", "AccessReport", "access_report"]
