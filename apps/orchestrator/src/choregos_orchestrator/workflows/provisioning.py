"""`ProjectProvisioning` : exécute les étapes du template, reprend là où ça a cassé."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import provisioning as provisioning_activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
NO_RETRY = RetryPolicy(maximum_attempts=1)


@dataclass
class ProvisioningInput:
    project_id: str
    project_slug: str
    org: str = ""
    template_ref: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    resume: bool = True


@workflow.defn(name="ProjectProvisioning", sandboxed=False)
class ProjectProvisioning:
    def __init__(self) -> None:
        self.current: str | None = None
        self.completed: list[str] = []
        self.failed: str | None = None

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"current": self.current, "completed": list(self.completed), "failed": self.failed}

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = ProvisioningInput(**payload)
        template = await workflow.execute_activity(
            provisioning_activities.load_template_steps,
            {"template_ref": params.template_ref},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY,
        )
        steps: list[dict[str, Any]] = template.get("steps", [])
        if params.dry_run:
            return {"steps": [step["name"] for step in steps], "dry_run": True}

        for step in steps:
            self.current = step["name"]
            try:
                outcome = await workflow.execute_activity(
                    provisioning_activities.run_provision_step,
                    {
                        "project_id": params.project_id,
                        "step": step["name"],
                        "params": {**step.get("params", {}), **params.inputs},
                        "resume": params.resume,
                    },
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RETRY,
                )
            except Exception:
                # L'étape a épuisé ses tentatives : on s'arrête ici, l'état reste repris au redémarrage.
                self.failed = step["name"]
                break
            self.completed.append(outcome["step"])
        self.current = None

        result = await workflow.execute_activity(
            provisioning_activities.finish_provisioning,
            {"project_id": params.project_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RETRY,
        )
        return {"completed": self.completed, "failed": self.failed, **result}
