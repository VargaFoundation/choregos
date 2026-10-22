# SPDX-License-Identifier: Apache-2.0
"""Building a hosted agent's zip (S41-08, ADR 0080 §9): the activities `HostedImageBuild` runs.

Idempotent (ADR 0008). `claim_hosted_build` reads the build's row and answers what is left to do;
`build_hosted_image` asks the builder, which finds an image already built under the key instead of
building it again; `record_hosted_build` writes the outcome once and, when the image is ready,
applies the channel moves publication deferred, each only when nothing newer took the channel.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

#: A build that fails on its inputs: the same inputs fail the same way, so it is not retried.
BUILD_FAILED = "hosted_build_failed"


def _key(payload: dict[str, Any]) -> tuple[str, str, int]:
    return str(payload["org"]), str(payload["key"]), int(payload["attempt"])


@activity.defn(name="claim_hosted_build")
async def claim_hosted_build(payload: dict[str, Any]) -> dict[str, Any]:
    """`building` when this attempt is the one to run, `ready` or `superseded` when there is
    nothing to do. A row not there yet is retried: the publication that started the workflow may
    not have committed."""
    from choregos_api.db.session import TOUT, session_scope
    from choregos_api.services.hosted_builds import build_row

    org, key, attempt = _key(payload)
    async with session_scope(orgs=TOUT) as session:
        row = await build_row(session, org, key)
        if row is None or row.attempt < attempt:
            raise ApplicationError(f"the build {key} (attempt {attempt}) is not recorded yet")
        if row.attempt > attempt:
            return {"status": "superseded", "attempt": row.attempt}
        if row.status == "ready":
            return {"status": "ready", "image": row.image}
        row.status = "building"
        return {"status": "building"}


@activity.defn(name="build_hosted_image")
async def build_hosted_image(payload: dict[str, Any]) -> dict[str, Any]:
    """The image of the build's key: the Dockerfile written again from a version that names it,
    checked against the key, and the zip from the object store as the context."""
    from choregos_adapters.oci import BuildError, BuildRequest
    from choregos_api.config import get_settings
    from choregos_api.db.session import TOUT, session_scope
    from choregos_api.object_store import object_store
    from choregos_api.schemas import AgentSpec, HostedRuntime
    from choregos_api.services.hosted_builds import image_builder, spec_of_build
    from choregos_api.services.hosted_sources import SourceRefused, build_key, dockerfile, load_source_bytes

    org, key, _ = _key(payload)
    async with session_scope(orgs=TOUT) as session:
        raw = await spec_of_build(session, org, key)
    if raw is None:
        raise ApplicationError(f"no version names the build {key}", type=BUILD_FAILED, non_retryable=True)
    runtime = AgentSpec.model_validate(raw).runtime
    assert isinstance(runtime, HostedRuntime) and runtime.source.type == "zip"
    repository = get_settings().hosted_build_repository
    if not repository:
        raise ApplicationError(
            "no build repository is configured (CHOREGOS_HOSTED_BUILD_REPOSITORY, "
            "`global.images.buildRegistry` in the chart): the platform has nowhere to push the image",
            type=BUILD_FAILED,
            non_retryable=True,
        )
    try:
        data, archive = await load_source_bytes(object_store(), org, runtime.source.sha256)
        text = dockerfile(runtime, archive)
    except SourceRefused as exc:
        raise ApplicationError(str(exc), type=BUILD_FAILED, non_retryable=True) from exc
    if build_key(text, archive) != key:
        raise ApplicationError(
            f"the Dockerfile written today does not hash to the build {key} (its base image or "
            "template changed since publication): publish the version again",
            type=BUILD_FAILED,
            non_retryable=True,
        )
    try:
        result = await image_builder().build(BuildRequest(key, text, data, repository))
    except BuildError as exc:
        raise ApplicationError(str(exc), type=BUILD_FAILED, non_retryable=True) from exc
    activity.logger.info("hosted build %s: %s (built: %s)", key, result.image, result.built)
    return {"image": result.image, "builder": result.builder, "built": result.built}


@activity.defn(name="record_hosted_build")
async def record_hosted_build(payload: dict[str, Any]) -> dict[str, Any]:
    """Writes the outcome of this attempt, once; a ready image releases the deferred moves."""
    from choregos_api.db.session import TOUT, session_scope
    from choregos_api.services.agent_releases import release_built
    from choregos_api.services.hosted_builds import build_row
    from choregos_core import utcnow

    org, key, attempt = _key(payload)
    status = str(payload["status"])
    async with session_scope(orgs=TOUT) as session:
        row = await build_row(session, org, key)
        if row is None or row.attempt != attempt:
            return {"status": "superseded"}
        if row.status in ("ready", "failed"):  # recorded already: a retried activity
            return {"status": row.status, "moved": []}
        now = utcnow()
        row.status = status
        row.image = payload.get("image") if status == "ready" else None
        row.error = None if status == "ready" else str(payload.get("error") or "the build failed")[:4000]
        row.finished_at = now
        if status == "ready":
            row.provenance = {
                **(row.provenance or {}),
                "builder": str(payload.get("builder") or ""),
                "image": str(row.image),
                "built_at": now.isoformat(),
            }
        moved = await release_built(session, org, key) if status == "ready" else []
        return {"status": status, "moved": [f"{slug}@v{version}" for slug, version in moved]}
