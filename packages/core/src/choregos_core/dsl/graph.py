"""Rendu d'un workflow en graphe (couloirs par acteur) pour le front et la CLI."""

from __future__ import annotations

from typing import Any

from choregos_contracts import AgentActor, HumanActor, Workflow
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
    """Graphe {nodes, edges} consommé par React Flow (`/p/{slug}/workflow`)."""
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
    for t in wf.transitions:
        actor = wf.actors.get(t.by) if t.by else None
        actor_type = None
        if actor is not None:
            actor_type = str(actor.type)
        base = {
            "id": t.key,
            "to": t.to,
            "actor": t.by,
            "actor_type": actor_type or ("train" if t.via else None),
            "role": str(actor.role) if isinstance(actor, AgentActor) else None,
            "gates": t.gate_names(),
            "via": t.via,
            "timeout_hours": t.timeout_hours,
        }
        sources = agent_states if t.from_ == AGENT_WILDCARD else [t.from_]
        for src in sources:
            edges.append({**base, "from": src, "wildcard": t.from_ == AGENT_WILDCARD})
    return {"nodes": nodes, "edges": edges}


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
