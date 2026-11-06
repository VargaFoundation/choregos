# SPDX-License-Identifier: Apache-2.0
"""The repository an activity talks to: the work item's target (ADR 0124), never `config.repo`.

`choregos_core.repositories.target` decides; this module turns its refusal into what the workflow
reads — a non-retryable `RepositoryRefused` the interpreter escalates like any configuration refusal —
and records the target the first time git is touched, so a work item keeps one repository.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from choregos_core.domain import REPOSITORY_FIELD
from choregos_core.repositories import RepositoryRefusedError, Target, fixing, target
from temporalio.exceptions import ApplicationError

#: The error type of a target the project cannot serve: a name it does not hold, or another than the
#: one the work item is fixed to. A person sets the field right, then replays the stage.
REPOSITORY_REFUSED = "RepositoryRefused"


def repo_slug(url: str) -> str:
    """`https://github.com/acme/billing.git` → `acme/billing`."""
    trimmed = url.removesuffix(".git").rstrip("/")
    parts = trimmed.split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else trimmed


def target_of(bundle: Any, item: Any) -> Target | None:
    """The work item's target, `None` for a project without a repository; refused, non-retryable,
    when the work item names a repository its project does not serve (ADR 0124 §4)."""
    try:
        return target(bundle.config, item.fields, item.documents)
    except RepositoryRefusedError as refused:
        raise ApplicationError(str(refused), type=REPOSITORY_REFUSED, non_retryable=True) from refused


def target_or_none(bundle: Any, item: Any) -> Target | None:
    """The target, or `None` when there is none to read: a guarantee then refuses for want of a diff."""
    try:
        return target(bundle.config, item.fields, item.documents)
    except RepositoryRefusedError:
        return None


def fix(bundle: Any, item: Any, found: Target) -> None:
    """Record the target at the work item's first git activity (ADR 0124 §3); a no-op afterwards and
    on a project with one repository."""
    documents = fixing(bundle.config, item.documents, found)
    if documents is not None:
        item.documents = documents


def adopted(outputs: Mapping[str, Any], declared: Iterable[str]) -> str | None:
    """The repository a successful step names in an output its step declares (`outputs: [repository]`,
    or a decision question named `repository`), copied into `fields.repository` and checked at its
    first use (ADR 0124 §2). `None` when the step does not declare it or answered nothing usable."""
    if REPOSITORY_FIELD not in set(declared):
        return None
    value = outputs.get(REPOSITORY_FIELD)
    return value.strip() if isinstance(value, str) and value.strip() else None


def adopt(item: Any, outputs: Mapping[str, Any], declared: Iterable[str]) -> None:
    """Copy a declared `repository` output into the work item's fields."""
    name = adopted(outputs, declared)
    if name is not None:
        item.fields = {**(item.fields or {}), REPOSITORY_FIELD: name}


__all__ = [
    "REPOSITORY_REFUSED",
    "adopt",
    "adopted",
    "fix",
    "repo_slug",
    "target_of",
    "target_or_none",
]
