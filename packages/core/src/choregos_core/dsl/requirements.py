# SPDX-License-Identifier: Apache-2.0
"""What a project's workflows require of its connectors (ADR 0034).

The product review of 2026-10-05: the settings of an HR project showed `scm`, `ci`, `cd`, that is
a repository, continuous integration and a train it does not have. What a project must connect is
DEDUCED from what its workflows do:

- a guarantee reads capabilities (`scope_respected` a diff; `ci_green` the PR's checks, through the
  repository, which was not the CI, something nothing reads): `gate_needs`;
- an agent whose role works in a repository (`implement`, `fix_ci`...) requires an `scm`;
- a release train, a production state (`production: true`, or `deployed_prod*` for a workflow
  written before #175) or a check in prod require a `cd`;
- a system transition that opens or merges a PR (`does`, or a `pr_*` / `merged*` state for a
  workflow written before #175) requires an `scm`;
- agents run somewhere (`runtime`) and call models (`gateway`);
- work items live in a `tracker`: the internal one, until another is connected.

Each requirement says WHY: the console shows the reason, not only the box.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from choregos_contracts import SystemActor, Workflow
from choregos_contracts.enums import ActorType
from choregos_contracts.workflow import is_production_state, transition_effect

from ..gates import gate_needs

#: The roles whose playbook works in a repository: they read a diff, push a branch.
ROLES_IN_A_REPOSITORY = frozenset(
    {"implement", "fix_ci", "address_review", "review", "release_notes", "architect", "document"}
)
#: Those that look at production.
ROLES_IN_PRODUCTION = frozenset({"verify_prod"})

#: The order in which the console shows them.
ORDER = ("tracker", "scm", "ci", "cd", "runtime", "gateway", "memory", "notify")


@dataclass(frozen=True)
class Requirement:
    capability: str
    reasons: tuple[str, ...]

    @property
    def capacite(self) -> str:  # lang: fr-ok alias:internal
        return self.capability

    @property
    def raisons(self) -> tuple[str, ...]:  # lang: fr-ok alias:internal
        return self.reasons


class _Reasons:
    """The reasons of each capability, in the order they are discovered, without duplicates."""

    def __init__(self) -> None:
        self.by_capability: dict[str, list[str]] = {
            "tracker": ["work items live in a tracker: the internal one unless you connect another"]
        }

    def require(self, capability: str, reason: str) -> None:
        reasons = self.by_capability.setdefault(capability, [])
        if reason not in reasons:
            reasons.append(reason)


def _actors(workflow: Workflow, reasons: _Reasons) -> None:
    name = workflow.metadata.name
    for actor, spec in workflow.actors.items():
        if spec.type == ActorType.JOB:
            reasons.require("runtime", f"{name}: the job {actor} runs somewhere")
            continue
        if spec.type != ActorType.AGENT:
            continue
        reasons.require("runtime", f"{name}: the agent {actor} runs somewhere")
        reasons.require("gateway", f"{name}: the agent {actor} calls models")
        role = str(getattr(spec, "role", ""))
        if role in ROLES_IN_A_REPOSITORY:
            reasons.require("scm", f"{name}: the agent {actor} ({role}) works in a repository")
        if role in ROLES_IN_PRODUCTION:
            reasons.require("cd", f"{name}: the agent {actor} ({role}) checks production")


def _states_and_transitions(workflow: Workflow, reasons: _Reasons) -> None:
    name = workflow.metadata.name
    for state, spec in workflow.states.items():
        if is_production_state(state, spec):
            reasons.require("cd", f"{name}: the state {state} is production")
    for transition in workflow.transitions:
        ident = transition.id or f"{transition.from_}->{transition.to}"
        # What the platform DOES when it crosses a system transition (`does`, #175), deduced from the
        # name of the target state for a workflow written before.
        effect = transition_effect(transition)
        if effect and isinstance(workflow.actors.get(transition.by or ""), SystemActor):
            gesture = "opens" if effect == "open_pr" else "merges"
            reasons.require("scm", f"{name}: {ident} {gesture} a pull request")
        if transition.via == "release_train" or transition.train is not None:
            reasons.require("cd", f"{name}: {ident} goes through a release train")
        for guarantee in transition.gates:
            for capability in sorted(gate_needs(guarantee.name)):
                reasons.require(capability, f"{name}: the guarantee {guarantee.name} on {ident}")


def requirements(workflows: Iterable[Workflow]) -> list[Requirement]:
    """The capabilities these workflows require, each with its reasons (without duplicates)."""
    reasons = _Reasons()
    for workflow in workflows:
        _actors(workflow, reasons)
        _states_and_transitions(workflow, reasons)
    rank = {capability: index for index, capability in enumerate(ORDER)}
    return [
        Requirement(capability, tuple(items))
        for capability, items in sorted(
            reasons.by_capability.items(), key=lambda item: (rank.get(item[0], len(ORDER)), item[0])
        )
    ]


__all__ = ["ORDER", "ROLES_IN_A_REPOSITORY", "ROLES_IN_PRODUCTION", "Requirement", "requirements"]
