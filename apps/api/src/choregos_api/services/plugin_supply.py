# SPDX-License-Identifier: Apache-2.0
"""Where the installer obtains a loaded plugin (ADR 0093, S43-04).

The installer does not fetch: it asks a `PluginSupply` for the plugin `name` at `version` from
`source`, and receives a plugin that `choregos_core.plugins` has already loaded and validated. The
sources and their trust (OCI pull, cosign, pinned git, the supply-chain gate) are S43-03's; until they
are wired here, the deployment's offline directory (`CHOREGOS_PLUGINS_OFFLINE`) is the one supply, under
the source name `offline`.

`use_plugin_supply` replaces the supply (tests, and the seam S43-03 plugs its fetcher into).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol

import yaml
from choregos_core.plugins import MANIFEST, Plugin, opened

from ..config import Settings

OFFLINE = "offline"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


class PluginNotFoundError(LookupError):
    """No such source, plugin or version."""


class PluginVersionUnsupportedError(ValueError):
    """A version this supply cannot resolve (a range, before the sources of S43-03)."""


class PluginSupply(Protocol):
    """Obtains loaded plugins; the plugin's directory lives as long as the context."""

    def obtain(self, source: str, name: str, version: str | None) -> AbstractAsyncContextManager[Plugin]:
        """`version` is exact, or `None` for the newest one. Raises `PluginNotFoundError`,
        `PluginVersionUnsupportedError` or `choregos_core.plugins.PluginError`."""
        ...

    async def latest(self, source: str, name: str) -> str | None:
        """The newest version `source` offers for `name`, or `None`."""
        ...


def semver_key(version: str) -> tuple[int, int, int, int, str]:
    """Sorts semantic versions: a pre-release comes before its release. Not semver: sorts first."""
    found = _SEMVER.match(version)
    if found is None:
        return (-1, -1, -1, 0, version)
    major, minor, patch, pre = found.groups()
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


def is_exact(version: str) -> bool:
    return _SEMVER.match(version) is not None


class OfflineDirectory:
    """Plugin directories under one folder, at any depth up to two (`<name>/` or `<name>/<version>/`),
    served as the source `offline`."""

    def __init__(self, root: Path, source: str = OFFLINE) -> None:
        self.root = root
        self.source = source

    def _versions(self, source: str, name: str) -> dict[str, Path]:
        if source != self.source:
            raise PluginNotFoundError(f"no plugin source `{source}`")
        found: dict[str, Path] = {}
        if not self.root.is_dir():
            return found
        for manifest in sorted([*self.root.glob(f"*/{MANIFEST}"), *self.root.glob(f"*/*/{MANIFEST}")]):
            try:
                metadata = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("metadata") or {}
            except (yaml.YAMLError, UnicodeDecodeError, AttributeError):
                continue
            if metadata.get("name") == name and isinstance(metadata.get("version"), str):
                found.setdefault(metadata["version"], manifest.parent)
        return found

    @asynccontextmanager
    async def _obtain(self, source: str, name: str, version: str | None) -> AsyncIterator[Plugin]:
        if version is not None and not is_exact(version):
            raise PluginVersionUnsupportedError(
                f"`{version}` is a range: the offline directory installs an exact version"
            )
        versions = self._versions(source, name)
        if not versions:
            raise PluginNotFoundError(f"`{source}` offers no plugin `{name}`")
        chosen = version or max(versions, key=semver_key)
        if chosen not in versions:
            raise PluginNotFoundError(
                f"`{source}` offers `{name}` in {', '.join(sorted(versions))}, not {chosen}"
            )
        with opened(versions[chosen]) as plugin:
            yield plugin

    def obtain(self, source: str, name: str, version: str | None) -> AbstractAsyncContextManager[Plugin]:
        return self._obtain(source, name, version)

    async def latest(self, source: str, name: str) -> str | None:
        try:
            versions = self._versions(source, name)
        except PluginNotFoundError:
            return None
        return max(versions, key=semver_key) if versions else None


class _Nothing:
    """No source configured: every request is answered `PluginNotFoundError`."""

    @asynccontextmanager
    async def _obtain(self, source: str, name: str, version: str | None) -> AsyncIterator[Plugin]:
        raise PluginNotFoundError(f"no plugin source `{source}` is configured on this deployment")
        yield  # pragma: no cover - unreachable, makes this a generator

    def obtain(self, source: str, name: str, version: str | None) -> AbstractAsyncContextManager[Plugin]:
        return self._obtain(source, name, version)

    async def latest(self, source: str, name: str) -> str | None:
        return None


_override: PluginSupply | None = None


def plugin_supply(settings: Settings) -> PluginSupply:
    if _override is not None:
        return _override
    if settings.plugins_offline:
        return OfflineDirectory(Path(settings.plugins_offline))
    return _Nothing()


def use_plugin_supply(supply: PluginSupply | None) -> None:
    """Replaces the supply (tests, S43-03's fetcher), or forgets it."""
    global _override  # noqa: PLW0603 - one supply per process, like the object store
    _override = supply


__all__ = [
    "OFFLINE",
    "OfflineDirectory",
    "PluginNotFoundError",
    "PluginSupply",
    "PluginVersionUnsupportedError",
    "is_exact",
    "plugin_supply",
    "semver_key",
    "use_plugin_supply",
]
