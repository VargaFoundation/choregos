# SPDX-License-Identifier: Apache-2.0
"""Semantic versions and the version ranges of plugins (ADR 0093).

A range is what `plugin.schema.json` (`$defs.range`) admits: `^1.2`, `~0.1`, a bare version (`0.1.0`
exactly; `0.1` the 0.1 series; `1` the 1 series), or comparisons joined by commas
(`>=0.21.0,<0.23.0`, `~=1.4`). A partial version in a comparison is filled with zeros. Pre-releases
(`1.0.0-rc.1`) never satisfy a range unless it names that exact version: a range is a promise about
releases.

The core version a plugin's `core` range is checked against is the installed `choregos-core`
distribution's (`core_version()`), never a constant copied by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from ..errors import ChoregosError

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$"
)
_PARTIAL = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
_COMPARISON = re.compile(r"^(==|!=|>=|<=|~=|>|<)\s*(\d+(?:\.\d+){0,2})$")


class VersionError(ChoregosError):
    """A version or a range that cannot be read."""


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Version:
        found = _SEMVER.match(text.strip())
        if found is None:
            raise VersionError(f"`{text}` is not a semantic version")
        pre = tuple(found.group(4).split(".")) if found.group(4) else ()
        return cls(int(found.group(1)), int(found.group(2)), int(found.group(3)), pre)

    def _key(self) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
        # A release sorts after its pre-releases; numeric identifiers sort before alphanumeric ones.
        ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.pre)
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, ids)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}" + (f"-{'.'.join(self.pre)}" if self.pre else "")


def _partial(text: str) -> tuple[int, int | None, int | None]:
    found = _PARTIAL.match(text.strip())
    if found is None:
        raise VersionError(f"`{text}` is not a version")
    major, minor, patch = found.groups()
    return int(major), None if minor is None else int(minor), None if patch is None else int(patch)


def _filled(major: int, minor: int | None, patch: int | None) -> Version:
    return Version(major, minor or 0, patch or 0)


Bound = tuple[str, Version]


def _caret(major: int, minor: int | None, patch: int | None) -> list[Bound]:
    low = _filled(major, minor, patch)
    if major > 0 or minor is None:
        high = Version(major + 1, 0, 0)
    elif minor > 0 or patch is None:
        high = Version(0, minor + 1, 0)
    else:
        high = Version(0, 0, (patch or 0) + 1)
    return [(">=", low), ("<", high)]


def _series(major: int, minor: int | None, patch: int | None) -> list[Bound]:
    """`~1.2.3`, `~1.2`, `~1`, and the bare partial versions `1.2`, `1`."""
    low = _filled(major, minor, patch)
    high = Version(major + 1, 0, 0) if minor is None else Version(major, minor + 1, 0)
    return [(">=", low), ("<", high)]


def _comparison(operator: str, text: str) -> list[Bound]:
    major, minor, patch = _partial(text)
    if operator == "~=":  # PEP 440: ~=1.4 is >=1.4,<2; ~=1.4.5 is >=1.4.5,<1.5
        if minor is None:
            raise VersionError(f"`~={text}` needs at least two components")
        high = Version(major + 1, 0, 0) if patch is None else Version(major, minor + 1, 0)
        return [(">=", _filled(major, minor, patch)), ("<", high)]
    return [(operator, _filled(major, minor, patch))]


@dataclass(frozen=True, slots=True)
class Range:
    text: str
    bounds: tuple[Bound, ...]
    exact: Version | None = None

    @classmethod
    def parse(cls, text: str) -> Range:
        raw = text.strip()
        if not raw:
            raise VersionError("an empty version range")
        if _SEMVER.match(raw):
            version = Version.parse(raw)
            return cls(raw, (("==", version),), version)
        if raw[0] in "^~" and raw[:2] != "~=":
            major, minor, patch = _partial(raw[1:])
            fixed = _caret(major, minor, patch) if raw[0] == "^" else _series(major, minor, patch)
            return cls(raw, tuple(fixed))
        if _PARTIAL.match(raw):
            return cls(raw, tuple(_series(*_partial(raw))))
        bounds: list[Bound] = []
        for part in raw.split(","):
            found = _COMPARISON.match(part.strip())
            if found is None:
                raise VersionError(f"`{text}` is not a version range")
            bounds.extend(_comparison(found.group(1), found.group(2)))
        return cls(raw, tuple(bounds))

    def admits(self, version: Version | str) -> bool:
        candidate = Version.parse(version) if isinstance(version, str) else version
        if self.exact is not None:
            return candidate == self.exact
        if candidate.pre:
            return False
        return all(_holds(operator, candidate, bound) for operator, bound in self.bounds)


def _holds(operator: str, candidate: Version, bound: Version) -> bool:
    match operator:
        case "==":
            return candidate == bound
        case "!=":
            return candidate != bound
        case ">=":
            return candidate >= bound
        case "<=":
            return candidate <= bound
        case ">":
            return candidate > bound
        case _:
            return candidate < bound


def admits(range_text: str, version: str) -> bool:
    """Whether `version` is inside `range_text`; `VersionError` when either cannot be read."""
    return Range.parse(range_text).admits(version)


def core_version() -> str:
    """The running core's version: the installed `choregos-core` distribution's."""
    from importlib import metadata

    try:
        return metadata.version("choregos-core")
    except metadata.PackageNotFoundError:  # a tree that is not installed
        return "0.0.0"


__all__ = ["Range", "Version", "VersionError", "admits", "core_version"]
