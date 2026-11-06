# SPDX-License-Identifier: Apache-2.0
"""The repository a work item works on (ADR 0124): its target.

A project holds a primary repository (`repo`) and may hold others (`repositories`). A work item names
one by its reserved field `fields.repository`; absent, it works on the primary. The target is fixed at
the first activity that touches git (`documents.repository_fixed`): one work item, one repository, one
branch. Everything that talks to git asks `target`, never `config.repo`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from choregos_contracts import ProjectConfig, RepoConfig

from .domain import REPOSITORY_FIELD

#: The `documents` key that records the repository a work item is fixed to (ADR 0124 §3).
FIXED_KEY = "repository_fixed"
#: The tracker label that names a work item's repository when its fields do not: `repository:diamctl`.
LABEL_PREFIX = "repository:"


class RepositoryRefusedError(ValueError):
    """The work item names a repository its project does not hold, or another than the one it is fixed to."""


@dataclass(frozen=True, slots=True)
class Target:
    """The repository a work item works on: its name in the project and its configuration."""

    name: str
    repo: RepoConfig


def asked(fields: Mapping[str, Any] | None) -> str | None:
    """What `fields.repository` names, `None` when it says nothing."""
    value = (fields or {}).get(REPOSITORY_FIELD)
    return None if value is None or value == "" else str(value)


def fixed(documents: Mapping[str, Any] | None) -> str | None:
    """The repository the work item was fixed to at its first git activity, if it was."""
    value = (documents or {}).get(FIXED_KEY)
    return str(value) if value else None


def _holds(config: ProjectConfig) -> str:
    names = config.repository_names()
    return ", ".join(f"`{n}`" for n in names) if names else "none"


def unknown(config: ProjectConfig, value: Any) -> str | None:
    """Why a `fields.repository` a caller gives is refused, `None` when it names a repository of the
    project (or says nothing)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return "`fields.repository` is the name of one of the project's repositories, a string"
    if config.repo is None:
        return f"`fields.repository` names `{value}`, and the project {config.slug} holds no repository"
    if config.repository_named(value) is None:
        return f"`{value}` is not a repository of the project {config.slug}: it holds {_holds(config)}"
    return None


def target(
    config: ProjectConfig, fields: Mapping[str, Any] | None, documents: Mapping[str, Any] | None
) -> Target | None:
    """The repository the work item works on: the one it is fixed to, else `fields.repository`, else the
    primary. `None` for a project without a repository whose work item names none.

    Raises `RepositoryRefusedError` when the name designates no repository of the project, or when
    `fields.repository` names another repository than the fixed one (an output or a label written
    after the first git activity)."""
    wanted, kept = asked(fields), fixed(documents)
    if config.repo is None:
        if wanted is None and kept is None:
            return None
        raise RepositoryRefusedError(
            f"the work item names the repository `{wanted or kept}`, and the project {config.slug} holds none"
        )
    if kept is not None and wanted is not None and wanted != kept:
        raise RepositoryRefusedError(
            f"the work item works on `{kept}` since its first step on a repository, and its "
            f"`fields.repository` now says `{wanted}`: one work item works on one repository; set it back "
            "to the first, or open one work item per repository (a fan-out, ADR 0057)"
        )
    name = kept or wanted or str(config.primary_name)
    repo = config.repository_named(name)
    if repo is None:
        raise RepositoryRefusedError(
            f"`{name}` is not a repository of the project {config.slug}: it holds {_holds(config)}. "
            "Set the work item's repository to one of them"
        )
    return Target(name=name, repo=repo)


def fixing(
    config: ProjectConfig, documents: Mapping[str, Any] | None, found: Target
) -> dict[str, Any] | None:
    """The work item's documents with its target fixed, or `None` when there is nothing to record: a
    project with one repository has nothing to choose, and a fixed work item stays as it is."""
    if not config.repositories or fixed(documents) is not None:
        return None
    return {**(documents or {}), FIXED_KEY: found.name}


def fixed_name(
    config: ProjectConfig,
    fields: Mapping[str, Any] | None,
    documents: Mapping[str, Any] | None,
    *,
    has_pull_request: bool,
    cloned: bool,
) -> str | None:
    """The repository a work item can no longer leave, `None` while its target may still change.

    Recorded at its first git activity; a work item with a pull request, or a run that cloned a
    repository, is fixed too, on its current target: it ran before its project held a second one."""
    kept = fixed(documents)
    if kept is not None:
        return kept
    if config.repo is None or not (has_pull_request or cloned):
        return None
    return asked(fields) or config.primary_name


def from_labels(labels: Any) -> str | None:
    """The repository a `repository:<name>` label names, the first one; `None` without such a label."""
    for label in labels or ():
        text = str(label)
        if text.startswith(LABEL_PREFIX) and text[len(LABEL_PREFIX) :].strip():
            return text[len(LABEL_PREFIX) :].strip()
    return None


__all__ = [
    "FIXED_KEY",
    "LABEL_PREFIX",
    "RepositoryRefusedError",
    "Target",
    "asked",
    "fixed",
    "fixed_name",
    "fixing",
    "from_labels",
    "target",
    "unknown",
]
