# SPDX-License-Identifier: Apache-2.0
"""The former `CHOREGOS_*` environment variables, still read as `TRANIBOR_*` (ADR 0176, R4).

`TRANIBOR_<NAME>` is the name; `CHOREGOS_<NAME>` is its fallback for the deprecation window. This is the
one place that knows both, so no other module does:

* `adopt_legacy_environment` runs when a package of the platform is imported (before any setting is
  read). A process that has only `CHOREGOS_X` gets `TRANIBOR_X` with its value, and one warning per
  variable naming the new name (never the value). When both are set and differ, `TRANIBOR_X` wins and
  the warning says so. Every reader, a settings class, an `os.environ` read, a child process, then sees
  the new name only, whoever set the old one (a values file, a `.env`, an image's `ENV`).
* `with_legacy` is for what the platform hands to another process (a run's variables, a job's, an
  agent's): the receiver may be an older image or a script that reads the old name, so both are given.
* `is_platform_variable` is for the places that refuse or hide the platform's own variables (a plugin's
  `env`, an agent's): both prefixes count.

The fallback is removed in the minor that follows the removal of the import shims (ADR 0176 point 5).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "LEGACY_PREFIX",
    "PREFIX",
    "PREFIXES",
    "adopt_legacy_environment",
    "adopted",
    "is_platform_variable",
    "with_legacy",
    "with_legacy_entries",
]

PREFIX = "TRANIBOR_"
LEGACY_PREFIX = "CHOREGOS_"  # compat-ok
PREFIXES = (PREFIX, LEGACY_PREFIX)

_log = logging.getLogger("tranibor.env")
#: The variables already reported in this process: one warning each, however many packages import.
_reported: set[str] = set()


def is_platform_variable(name: str) -> bool:
    """Whether `name` is one of the platform's own, under either prefix."""
    return name.startswith(PREFIXES)


def _dotenv(path: Path) -> dict[str, str]:
    """The `KEY=value` pairs of a `.env` (the settings classes read it too), `{}` if unreadable."""
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except Exception:
        return {}


def adopt_legacy_environment(
    environ: MutableMapping[str, str] | None = None, dotenv: Mapping[str, str] | None = None
) -> list[str]:
    """Give each `CHOREGOS_X` of the environment (and of the working directory's `.env`) its `TRANIBOR_X`.

    Returns the names adopted or overridden this call (empty once everything has been reported).
    """
    target = os.environ if environ is None else environ
    file_values = _dotenv(Path(".env")) if dotenv is None and environ is None else dict(dotenv or {})
    seen = {**file_values, **target}  # an environment variable beats the file, as the settings classes do
    touched: list[str] = []
    for name, value in seen.items():
        if not name.startswith(LEGACY_PREFIX):
            continue
        new = PREFIX + name[len(LEGACY_PREFIX) :]
        if new in seen:
            if seen[new] != value and new not in _reported:
                _reported.add(new)
                _log.warning("%s and %s are both set and differ; %s is used", new, name, new)
                touched.append(new)
            continue
        target[new] = value
        if new not in _reported:
            _reported.add(new)
            _log.warning("%s is deprecated: set %s instead (its value is used as it is)", name, new)
            touched.append(new)
    return touched


def adopted(env: Mapping[str, str]) -> dict[str, str]:
    """`env` with, for each `CHOREGOS_X`, a `TRANIBOR_X` of the same value (unless one is given): what
    `adopt_legacy_environment` does to a process, for a mapping a caller builds itself."""
    both = dict(env)
    for name, value in env.items():
        if name.startswith(LEGACY_PREFIX):
            both.setdefault(PREFIX + name[len(LEGACY_PREFIX) :], value)
    return both


def with_legacy(env: Mapping[str, str]) -> dict[str, str]:
    """`env` with, for each `TRANIBOR_X`, a `CHOREGOS_X` of the same value (unless one is given)."""
    both = dict(env)
    for name, value in env.items():
        if name.startswith(PREFIX):
            both.setdefault(LEGACY_PREFIX + name[len(PREFIX) :], value)
    return both


def with_legacy_entries(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A container's `env` list (`{"name": ..., "value" | "valueFrom" | "secretRef": ...}`) with, after each
    `TRANIBOR_X` entry, a `CHOREGOS_X` one that carries the same value or the same secret."""
    out: list[dict[str, Any]] = []
    given = {str(entry.get("name")) for entry in entries}
    for entry in entries:
        out.append(dict(entry))
        name = str(entry.get("name", ""))
        if name.startswith(PREFIX) and LEGACY_PREFIX + name[len(PREFIX) :] not in given:
            out.append({**entry, "name": LEGACY_PREFIX + name[len(PREFIX) :]})
    return out
