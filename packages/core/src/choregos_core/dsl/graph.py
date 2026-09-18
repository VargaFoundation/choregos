"""Rendu d'un workflow en graphe (couloirs par acteur) pour le front et la CLI."""

from __future__ import annotations

from typing import Any

from choregos_contracts import AgentActor, HumanActor, StateKind, Workflow
from choregos_contracts.workflow import AGENT_WILDCARD


def _lane(wf: Workflow, state: str) -> str:
    """Couloir d'un état : le type de l'acteur qui en sort."""
    for t in wf.transitions:
        if t.from_ != state:
            continue
        if t.via == "release_train":
            return "train"
        actor = wf.actors.get(t.by) if t.by else None
        if isinstance(actor, AgentActor):
            return "agent"
        if isinstance(actor, HumanActor):
            return "human"
        return "system"
    return "terminal" if wf.states[state].terminal else "wait"


def to_graph(wf: Workflow) -> dict[str, Any]:
    """Graphe {nodes, edges} consommé par React Flow (`/p/{slug}/workflow`).

    Les arêtes nominales (`kind: "nominal"`) viennent des transitions ; les arêtes
    secondaires (rejet, reprise, escalade, défauts) sont rendues aussi, sinon les états
    d'escalade apparaîtraient orphelins sur la carte alors qu'ils sont bien atteignables.
    """
    nodes = [
        {
            "id": name,
            "display": state.display,
            "kind": str(state.kind),
            "terminal": state.terminal,
            "lane": _lane(wf, name),
            "tracker_status": state.tracker.status if state.tracker else None,
        }
        for name, state in wf.states.items()
    ]
    edges: list[dict[str, Any]] = []
    agent_states = sorted(wf.agent_driven_states())

    def add(source: str, target: str | None, kind: str, label: str, **extra: Any) -> None:
        if not target or target not in wf.states or source not in wf.states:
            return
        edges.append(
            {
                "id": f"{source}->{target}:{kind}:{label}",
                "from": source,
                "to": target,
                "kind": kind,
                "label": label,
                "wildcard": False,
                "actor": None,
                "actor_type": None,
                "role": None,
                "gates": [],
                "via": None,
                "timeout_hours": None,
                **extra,
            }
        )

    for t in wf.transitions:
        actor = wf.actors.get(t.by) if t.by else None
        base = {
            "actor": t.by,
            "actor_type": str(actor.type) if actor is not None else ("train" if t.via else None),
            "role": str(actor.role) if isinstance(actor, AgentActor) else None,
            "gates": t.gate_names(),
            "via": t.via,
            "timeout_hours": t.timeout_hours,
        }
        sources = agent_states if t.from_ == AGENT_WILDCARD else [t.from_]
        wildcard = t.from_ == AGENT_WILDCARD
        for src in sources:
            edges.append(
                {
                    **base,
                    "id": t.key if not wildcard else f"{t.key}:{src}",
                    "from": src,
                    "to": t.to,
                    "kind": "nominal",
                    "label": t.by or t.via or "",
                    "wildcard": wildcard,
                }
            )
            add(src, t.on_reject, "reject", "rejet", actor=t.by)
            if t.on_fail is not None:
                add(src, t.on_fail.to, "retry", f"échec (≤{t.on_fail.max_attempts})")
                add(src, t.on_fail.escalate_to, "escalate", "échecs épuisés")
            if t.on_changes_requested is not None:
                add(src, t.on_changes_requested.to, "retry", "changements demandés")
                add(src, t.on_changes_requested.escalate_to, "escalate", "revues épuisées")

    defaults = wf.defaults
    if defaults and defaults.from_any_agent_state:
        d = defaults.from_any_agent_state
        for src in agent_states:
            add(src, d.on_question, "default", "question")
            add(src, d.on_budget_exceeded, "default", "budget dépassé")
            add(src, d.on_timeout, "default", "délai dépassé")
    if defaults and defaults.needs_human and defaults.needs_human.on_abandon:
        # Même convention que le validateur : l'état où un humain est garé.
        for name, state in wf.states.items():
            if name == "needs_human" or state.kind == StateKind.WAIT:
                add(name, defaults.needs_human.on_abandon, "default", "abandon")

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for edge in edges:
        if edge["id"] in seen:
            continue
        seen.add(edge["id"])
        unique.append(edge)
    return {"nodes": nodes, "edges": unique}


def to_mermaid(wf: Workflow) -> str:
    """Rendu Mermaid (documentation, `choregos workflow show`)."""
    lines = ["stateDiagram-v2", f"  [*] --> {wf.initial_state}"]
    for t in wf.transitions:
        actor = wf.actors.get(t.by) if t.by else None
        label = t.by or t.via or ""
        if isinstance(actor, AgentActor):
            label = f"{t.by} ({actor.role})"
        gates = ",".join(t.gate_names())
        if gates:
            label = f"{label} [{gates}]"
        src = "agent_states" if t.from_ == AGENT_WILDCARD else t.from_
        lines.append(f"  {src} --> {t.to}: {label}")
    for name in wf.terminal_states():
        lines.append(f"  {name} --> [*]")
    return "\n".join(lines)
