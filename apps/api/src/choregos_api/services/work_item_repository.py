# SPDX-License-Identifier: Apache-2.0
"""A work item's target repository, where a caller can be answered (ADR 0124).

`fields.repository` names one of the project's repositories; absent, the work item works on the
primary. A caller who gives it — a person, the API, a door, a trigger, a schedule, a fan-out — is
refused at once when it names none of them (422), and a person may not move a work item that is
fixed to its repository (409). A tracker label is copied as written: the tracker cannot be answered,
and the first stage that touches git refuses an unknown name (the orchestrator's `RepositoryRefused`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from choregos_contracts import ProjectConfig
from choregos_core.domain import REPOSITORY_FIELD
from choregos_core.repositories import asked, fixed, fixed_name, from_labels, unknown
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Project, Run, WorkItem
from ..errors import conflict, unprocessable


def config_of(project: Project) -> ProjectConfig | None:
    """The project's configuration as the model reads it; `None` for one stored before a field it no
    longer accepts (a work item is still readable then)."""
    try:
        return ProjectConfig.model_validate(project.config or {})
    except ValidationError:
        return None


def refuse_unknown_repository(project: Project, fields: Mapping[str, Any]) -> None:
    """A `fields.repository` that names no repository of the project: 422, with the names it holds."""
    if fields.get(REPOSITORY_FIELD) is None:
        return
    config = config_of(project)
    why = unknown(config, fields[REPOSITORY_FIELD]) if config is not None else None
    if why is not None:
        raise unprocessable(why, [{"loc": ["fields", REPOSITORY_FIELD], "msg": "unknown repository"}])


def birth_fields(project: Project, fields: Mapping[str, Any], labels: Iterable[str]) -> dict[str, Any]:
    """The fields a work item is born with: a given `repository` is checked; without one, a
    `repository:<name>` label names it, copied as written (ADR 0124 §2)."""
    refuse_unknown_repository(project, fields)
    if fields.get(REPOSITORY_FIELD) is None and (labelled := from_labels(labels)) is not None:
        # A project without a repository has nothing to choose: its label is a tag like any other.
        config = config_of(project)
        if config is not None and config.repo is not None:
            return {**fields, REPOSITORY_FIELD: labelled}
    return dict(fields)


async def _cloned(session: AsyncSession, item: WorkItem) -> bool:
    """Whether one of the work item's runs cloned a repository (its recorded `StageInput.repo`)."""
    inputs = (await session.execute(select(Run.stage_input).where(Run.work_item_id == item.id))).scalars()
    return any(isinstance(recorded, dict) and recorded.get("repo") for recorded in inputs)


async def fixed_repository(session: AsyncSession, config: ProjectConfig, item: WorkItem) -> str | None:
    """The repository the work item can no longer leave (ADR 0124 §3), `None` while it may change."""
    if (kept := fixed(item.documents)) is not None or config.repo is None:
        return kept
    # A run that cloned before its project held a second repository fixes it too; asked only where a
    # choice exists and nothing else said it.
    cloned = bool(config.repositories) and not item.pr_url and await _cloned(session, item)
    return fixed_name(config, item.fields, item.documents, has_pull_request=bool(item.pr_url), cloned=cloned)


async def refuse_repository_change(
    session: AsyncSession, project: Project, item: WorkItem, change: Mapping[str, Any]
) -> None:
    """A person's change of `fields.repository` (`PATCH`, a task's values): an unknown name is 422, and
    another repository than the one the work item is fixed to is 409."""
    if REPOSITORY_FIELD not in change:
        return
    refuse_unknown_repository(project, change)
    config = config_of(project)
    if config is None or config.repo is None:
        return
    kept = await fixed_repository(session, config, item)
    wanted = change[REPOSITORY_FIELD] or config.primary_name
    if kept is not None and wanted != kept:
        raise conflict(
            f"the work item works on `{kept}` since its first step on a repository: one work item works on "
            "one repository; open another work item for the other one (ADR 0124)"
        )


async def repository_view(session: AsyncSession, project: Project, item: WorkItem) -> tuple[str | None, bool]:
    """What the work item's page says: the repository it works on, and whether that is fixed."""
    config = config_of(project)
    if config is None or config.repo is None:
        return None, False
    kept = await fixed_repository(session, config, item)
    return kept or asked(item.fields) or config.primary_name, kept is not None


__all__ = [
    "birth_fields",
    "config_of",
    "fixed_repository",
    "refuse_repository_change",
    "refuse_unknown_repository",
    "repository_view",
]
