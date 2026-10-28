# SPDX-License-Identifier: Apache-2.0
"""Where the installer obtains a loaded plugin (ADR 0093, S43-04, S43-05).

The installer does not fetch: it asks a `PluginSupply` for the plugin `name` at `version` from
`source`, and receives a plugin that `choregos_core.plugins` has already loaded and validated.

- `SourcesSupply` is the deployment's: every declared source of `CHOREGOS_PLUGINS_CONFIG` and the
  offline directory, through `plugin_sources.fetch_plugin`, so every install and upgrade passes the
  supply-chain checks (index, signature, git pin, digest, lock, content, core range). It is what the
  routes and the start-up reconcile use as soon as a configuration file or an offline directory is set.
- `OfflineDirectory` reads plugin directories (`<name>/` or `<name>/<version>/`) with no index, lock or
  signature: the tests' supply, installed with `use_plugin_supply`.

The second approval a plugin with a server needs (ADR 0050) is not the supply's: the routes assess every
plugin they obtain, whatever the supply (`plugin_sources.assess_plugin`).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol

import yaml
from choregos_core.plugins import MANIFEST, Plugin, opened
from choregos_core.plugins.sources import SourceError

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


class SourcesSupply:
    """The deployment's sources (`CHOREGOS_PLUGINS_CONFIG`, `CHOREGOS_PLUGINS_OFFLINE`) through
    `fetch_plugin`: a refusal is a `SourceError` naming the check."""

    @asynccontextmanager
    async def _obtain(self, source: str, name: str, version: str | None) -> AsyncIterator[Plugin]:
        from .plugin_sources import fetch_plugin

        async with fetch_plugin(source, name, version or "*") as fetched:
            yield fetched.plugin

    def obtain(self, source: str, name: str, version: str | None) -> AbstractAsyncContextManager[Plugin]:
        return self._obtain(source, name, version)

    async def latest(self, source: str, name: str) -> str | None:
        from choregos_core.plugins.sources import resolve
        from choregos_core.plugins.versions import core_version

        from .plugin_sources import plugins_config, read_index

        try:
            config = plugins_config()
            index = await read_index(config.source(source), config)
            return resolve(index, name, "*", core_version()).version
        except SourceError:
            return None


_override: PluginSupply | None = None


def plugin_supply(settings: Settings) -> PluginSupply:
    if _override is not None:
        return _override
    if settings.plugins_config or settings.plugins_offline:
        return SourcesSupply()
    return _Nothing()


def use_plugin_supply(supply: PluginSupply | None) -> None:
    """Replaces the supply (tests), or forgets it: the deployment's sources then serve again."""
    global _override  # noqa: PLW0603 - one supply per process, like the object store
    _override = supply


__all__ = [
    "OFFLINE",
    "OfflineDirectory",
    "PluginNotFoundError",
    "PluginSupply",
    "PluginVersionUnsupportedError",
    "SourceError",
    "SourcesSupply",
    "is_exact",
    "plugin_supply",
    "semver_key",
    "use_plugin_supply",
]
