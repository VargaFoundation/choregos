# SPDX-License-Identifier: Apache-2.0
"""The build of a hosted agent's zip (ADR 0080 §9): one row per organisation and build key, built
once by the `HostedImageBuild` workflow, and what a channel waits for.

A zip version is published with `runtime.build` as the platform wrote it (`pending`, its key, its
provenance) and keeps it: versions are immutable. The `hosted_builds` row is the truth about the
build since: its status, its image, why it failed. Every version with the same key shares it, so a
second version of the same zip starts nothing, and a version whose build is ready runs at once.

Publication starts the build (`ensure_build`); production and testing do not move until it is
`ready` (`agent_releases.after_publish` defers, `release_built` applies the move once built), and
a release of a version whose build is not ready fails its `hosted_build` check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from choregos_adapters.registry import fakes_enabled
from sqlalchemy import ColumnElement, String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AgentVersion, HostedBuildRecord
from ..logging import get_logger
from ..temporal import get_temporal, hosted_build_id

if TYPE_CHECKING:
    from choregos_adapters.base import ImageBuilder

    from ..schemas import AgentSpec

log = get_logger(__name__)

#: Tests and `CHOREGOS_FAKES` put their builder here.
_OVERRIDES: dict[str, Any] = {}


def set_image_builder(builder: ImageBuilder | None) -> None:
    """For tests and the fakes: the builder the build activity uses (`None` forgets it)."""
    _OVERRIDES.clear()
    if builder is not None:
        _OVERRIDES["builder"] = builder


def image_builder() -> ImageBuilder:
    if "builder" in _OVERRIDES:
        found: ImageBuilder = _OVERRIDES["builder"]
        return found
    if fakes_enabled():
        from choregos_adapters.fakes import FakeImageBuilder

        return _OVERRIDES.setdefault("builder", FakeImageBuilder())  # type: ignore[no-any-return]
    from choregos_adapters.oci import BuildctlBuilder, LocalDockerBuilder, OciRegistryClient

    settings = get_settings()
    # The build repository is the deployment's own: not an organisation's egress.
    registry = OciRegistryClient()
    if settings.hosted_builder == "local_docker":
        return LocalDockerBuilder(registry)
    return BuildctlBuilder(settings.buildkit_address, registry, tls_dir=settings.buildkit_tls_dir)


def zip_build_key(spec: AgentSpec | Mapping[str, Any] | None) -> str | None:
    """The build key of a zip version, read from its spec as stored or validated; `None` otherwise."""
    if spec is None:
        return None
    raw: Mapping[str, Any] = spec if isinstance(spec, Mapping) else spec.model_dump(mode="json")
    runtime = raw.get("runtime") or {}
    if runtime.get("kind") != "hosted" or (runtime.get("source") or {}).get("type") != "zip":
        return None
    key = (runtime.get("build") or {}).get("key")
    return str(key) if key else None


def names_key(key: str) -> ColumnElement[bool]:
    """A cheap filter on `AgentVersion.spec` for the versions that may carry this key, on SQLite as
    on PostgreSQL; `zip_build_key` confirms each one."""
    return cast(AgentVersion.spec, String).contains(key.removeprefix("sha256:"))


async def spec_of_build(session: AsyncSession, org_id: str, key: str) -> dict[str, Any] | None:
    """The spec of a version built from this key: they all say the same of the build."""
    rows = await session.execute(
        select(AgentVersion.spec).where(AgentVersion.org_id == org_id, names_key(key))
    )
    return next((spec for spec in rows.scalars() if zip_build_key(spec) == key), None)


async def build_row(session: AsyncSession, org_id: str, key: str) -> HostedBuildRecord | None:
    return (
        await session.execute(
            select(HostedBuildRecord).where(HostedBuildRecord.org_id == org_id, HostedBuildRecord.key == key)
        )
    ).scalar_one_or_none()


async def builds_for(
    session: AsyncSession, org_id: str, specs: Iterable[AgentSpec | Mapping[str, Any] | None]
) -> dict[str, HostedBuildRecord]:
    """The build rows of these versions' zips, by key: one query for a list of versions."""
    keys = {key for spec in specs if (key := zip_build_key(spec)) is not None}
    if not keys:
        return {}
    rows = await session.execute(
        select(HostedBuildRecord).where(HostedBuildRecord.org_id == org_id, HostedBuildRecord.key.in_(keys))
    )
    return {row.key: row for row in rows.scalars()}


def with_current_build(spec: AgentSpec, builds: Mapping[str, HostedBuildRecord]) -> AgentSpec:
    """The spec as an API answer shows it: a zip version's `build` as its row says now."""
    from ..schemas import HostedRuntime

    key = zip_build_key(spec)
    row = builds.get(key) if key else None
    runtime = spec.runtime
    if row is None or not isinstance(runtime, HostedRuntime) or runtime.build is None:
        return spec
    build = runtime.build.model_copy(
        update={
            "status": row.status,
            "image": row.image,
            "error": row.error,
            "provenance": {**runtime.build.provenance, **_strings(row.provenance)},
        }
    )
    return spec.model_copy(update={"runtime": runtime.model_copy(update={"build": build})})


def _strings(values: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(k): str(v) for k, v in (values or {}).items() if v is not None}


async def ensure_build(
    session: AsyncSession, org_id: str, spec: AgentSpec | Mapping[str, Any]
) -> HostedBuildRecord | None:
    """The build row of a zip version, created and started when there is none, started again as
    the next attempt when it failed. A build pending or running is started again too: the workflow
    id makes it a no-op when it runs, and a start lost with a crashed request is made good.
    `None` for a version that is not built from a zip."""
    key = zip_build_key(spec)
    if key is None:
        return None
    row = await build_row(session, org_id, key)
    if row is None:
        raw = spec if isinstance(spec, Mapping) else spec.model_dump(mode="json")
        provenance = ((raw.get("runtime") or {}).get("build") or {}).get("provenance") or {}
        row = HostedBuildRecord(
            org_id=org_id, key=key, status="pending", attempt=1, provenance=_strings(provenance)
        )
        session.add(row)
        await session.flush()
    elif row.status == "ready":
        return row
    elif row.status == "failed":
        row.attempt += 1
        row.status, row.image, row.error, row.finished_at = "pending", None, None, None
        await session.flush()
    await get_temporal().start_hosted_build(
        hosted_build_id(org_id, key, row.attempt), {"org": org_id, "key": key, "attempt": row.attempt}
    )
    log.info("hosted.build_started", org=org_id, key=key, attempt=row.attempt)
    return row


async def build_is_ready(
    session: AsyncSession, org_id: str, spec: AgentSpec | Mapping[str, Any] | None
) -> bool:
    """Whether a version may enter a channel as far as its build goes: always, unless it is built
    from a zip whose build is not `ready`."""
    key = zip_build_key(spec)
    if key is None:
        return True
    row = await build_row(session, org_id, key)
    return row is not None and row.status == "ready"
