# SPDX-License-Identifier: Apache-2.0
"""The step effects of software delivery (ADR 0181, round C2): `does: open_pr` and `does: merge_pr`,
registered by the plugin's extension under the names stored workflows carry.

`open_pr` runs when its transition is taken, before the gates (they read the pull request it opened);
`merge_pr` once every gate passed, at the commit they judged (ADR 0171). Both use the `scm`. Their
activities (`open_pull_request`, `enqueue_merge`) are still the orchestrator's, played by the interpreter
by name, so every recorded history replays unchanged; round D gives them a worker seam.
"""

from __future__ import annotations

from tranibor_core.step_effects import ENTERED, PASSED, step_effect

from .names import NAME

#: name -> (what the platform does, in plain words, and when).
EFFECTS = {
    "open_pr": ("opens the pull request", ENTERED),
    "merge_pr": ("merges the pull request", PASSED),
}


def register_effects() -> None:
    """Register the two effects through the core's step-effect seam. Idempotent."""
    for name, (gesture, when) in EFFECTS.items():
        step_effect(name, gesture=gesture, when=when, plugin=NAME, execute=None, needs=("scm",))
