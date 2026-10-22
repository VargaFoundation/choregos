# SPDX-License-Identifier: Apache-2.0
"""`HostedImageBuild`: the image of a hosted agent's zip (S41-08, ADR 0080 §9).

Started by the publication of a zip version, one per organisation, build key and attempt. It
claims the build, has the executor queue build it (the builder finds an image already built under
the key rather than build it twice), and records the outcome, which releases the channel moves
publication deferred. A build that fails is recorded `failed` with its reason; publishing the
version again starts the next attempt.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import hosted_build as build_activities

#: The publication's transaction may commit after the workflow starts: the claim waits for it.
CLAIM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=10,
)
BUILD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=[build_activities.BUILD_FAILED],
)
RECORD_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=10)


def _reason(error: ActivityError) -> str:
    cause = error.cause
    return str(getattr(cause, "message", None) or cause or error)


@workflow.defn(name="HostedImageBuild", sandboxed=False)
class HostedImageBuild:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        build = {"org": payload["org"], "key": payload["key"], "attempt": int(payload["attempt"])}
        claim = await executer_activite(
            build_activities.claim_hosted_build,
            build,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=CLAIM_RETRY,
        )
        if claim["status"] != "building":
            return dict(claim)
        try:
            built = await executer_activite(
                build_activities.build_hosted_image,
                build,
                start_to_close_timeout=timedelta(minutes=45),
                retry_policy=BUILD_RETRY,
            )
            outcome = {**build, "status": "ready", "image": built["image"], "builder": built["builder"]}
        except ActivityError as error:
            outcome = {**build, "status": "failed", "error": _reason(error)}
        recorded = await executer_activite(
            build_activities.record_hosted_build,
            outcome,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RECORD_RETRY,
        )
        return dict(recorded)
