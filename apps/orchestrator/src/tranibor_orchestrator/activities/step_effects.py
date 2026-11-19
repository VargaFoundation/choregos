# SPDX-License-Identifier: Apache-2.0
"""An extension's step effect, played when its transition is crossed (`does:`, ADR 0181 round C0).

The interpreter does not read the step-effect registry: it calls this activity for any `does:` that is
not the core's own (`open_pr`, `merge_pr`, which keep their activities), at each of the two moments,
and this activity, in the worker that loaded the extensions, decides. What it answers is recorded in the
history, so a history replays without the extension installed.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from tranibor_core.step_effects import PASSED, StepEffectRequest, step_effect_of

from .base import db, project_bundle


@activity.defn(name="run_step_effect")
async def run_step_effect(payload: dict[str, Any]) -> dict[str, Any]:
    """Play the effect `payload["effect"]` if it runs at `payload["moment"]`.

    Answers `ok` (false: the transition's guarantees are refused, with `detail`), `ran`, and `later`
    (true at the `entered` moment for an effect that runs once the gates have passed). An effect no
    installed extension registers is refused: a workflow that names it was published where it was."""
    name = str(payload["effect"])
    moment = str(payload["moment"])
    found = step_effect_of(name)
    if found is None or found.execute is None:
        return {
            "ok": False,
            "ran": False,
            "later": False,
            "detail": f"the step effect `{name}` is not installed in this deployment: install the extension "
            "of the plugin that declares it",
        }
    if found.when != moment:
        return {"ok": True, "ran": False, "later": found.when == PASSED, "detail": ""}
    transition, attempt = str(payload["transition"]), int(payload.get("attempt", 1))
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        request = StepEffectRequest(
            effect=name,
            project_id=str(payload["project_id"]),
            work_item_id=str(payload["work_item_id"]),
            transition=transition,
            attempt=attempt,
            key=f"{payload.get('workflow_id', '')}:{transition}:{attempt}:{name}",
            project=bundle,
            session=session,
        )
        try:
            said = await found.execute(request)
        except Exception as error:  # the plugin's failure is a refusal of the crossing, said with its name
            return {
                "ok": False,
                "ran": True,
                "later": False,
                "detail": f"`does: {name}` of the plugin `{found.plugin}` failed: {error}",
            }
    detail = str((said or {}).get("detail", "")) if isinstance(said, dict) else ""
    return {"ok": True, "ran": True, "later": False, "detail": detail}
