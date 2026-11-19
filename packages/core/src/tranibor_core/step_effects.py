# SPDX-License-Identifier: Apache-2.0
"""The step-effect registry: what `does:` names, resolved by name (ADR 0181, round C0).

`does:` on a system transition says what the PLATFORM does when it takes it. It was a closed list,
`open_pr` and `merge_pr`, written into the contract. It is now a name resolved here: the core registers
those two under the same names (they become the `software-delivery` plugin's in round C2), and an
extension registers its own from its `choregos.plugins` entry point with `step_effect(...)`, which also
declares the name in the contract's open list (`tranibor_contracts.STEP_EFFECTS`), so that a workflow
naming it validates.

An effect runs at one of two moments of its transition:

- `ENTERED`: when the transition is taken, before its gates are judged (`open_pr`: the gates then read
  the pull request it opened);
- `PASSED`: once every gate of the transition has passed (`merge_pr`: nothing is merged on a refusal).

The core's two keep their own activities in the interpreter, so a recorded history replays unchanged.
An extension's effect is played by one activity, `run_step_effect`, which looks the effect up in the
worker's registry and calls its `execute`: the interpreter itself never reads this registry, so a history
replays without the extension installed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from tranibor_contracts import STEP_EFFECTS

Moment = Literal["entered", "passed"]
#: When the transition is taken, before its gates.
ENTERED: Moment = "entered"
#: Once every gate of the transition has passed.
PASSED: Moment = "passed"

#: The effects the core plays with its own activities (the interpreter names them).
CORE_EFFECTS = ("open_pr", "merge_pr")


@dataclass(slots=True, frozen=True)
class StepEffectRequest:
    """What an effect's `execute` is given. `project` and `session` are the project as the worker holds
    it and the activity's database session, opaque to the core. `execute` must be idempotent: the
    activity is retried, and the platform's own key for this crossing is `key`."""

    effect: str
    project_id: str
    work_item_id: str
    transition: str
    attempt: int
    key: str
    project: Any = None
    session: Any = None


Execute = Callable[[StepEffectRequest], Awaitable[Mapping[str, Any] | None]]


@dataclass(slots=True, frozen=True)
class StepEffect:
    """One effect `does:` can name."""

    name: str
    #: What it does, in plain words after "the platform" (`opens the pull request`): the process view
    #: and the validator's messages read it.
    gesture: str
    when: Moment
    #: The connector capabilities it uses (ADR 0034): a workflow naming it requires them of its project.
    needs: frozenset[str] = frozenset()
    plugin: str | None = None
    execute: Execute | None = field(default=None, compare=False)


_EFFECTS: dict[str, StepEffect] = {}


def step_effect(
    name: str,
    *,
    gesture: str,
    when: Moment,
    plugin: str,
    execute: Execute,
    needs: tuple[str, ...] = (),
) -> StepEffect:
    """Register an extension's step effect, and declare its name in the contract's open list.

    A name the core or another plugin registered is refused, naming its owner."""
    if when not in (ENTERED, PASSED):
        raise ValueError(f"a step effect runs `{ENTERED}` or `{PASSED}`, not `{when}`")
    existing = _EFFECTS.get(name)
    if existing is not None and existing.plugin != plugin:
        owner = f"the plugin `{existing.plugin}`" if existing.plugin else "the core"
        raise ValueError(f"the step effect `{name}` is already registered by {owner}")
    STEP_EFFECTS.declare(name, plugin=plugin)
    effect = StepEffect(name, gesture, when, frozenset(needs), plugin, execute)
    _EFFECTS[name] = effect
    return effect


def _core(name: str, gesture: str, when: Moment) -> None:
    _EFFECTS[name] = StepEffect(name, gesture, when, frozenset({"scm"}))


_core("open_pr", "opens the pull request", ENTERED)
_core("merge_pr", "merges the pull request", PASSED)


def step_effect_of(name: str | None) -> StepEffect | None:
    """The registered effect of that name; `None` for none, or one no installed extension registers."""
    return _EFFECTS.get(name) if name else None


def known_step_effects() -> list[str]:
    return sorted(_EFFECTS)


def forget(plugin: str) -> None:
    """Drop what `plugin` registered (a test that installed an extension does it after)."""
    for name in [name for name, effect in _EFFECTS.items() if effect.plugin == plugin]:
        del _EFFECTS[name]
    STEP_EFFECTS.forget(plugin)
