# SPDX-License-Identifier: Apache-2.0
"""The plugins a deployment installs by default (ADR 0093 §5, plan Decision 5, S43-05).

The chart's `plugins.defaultPlugins` (source, name, version range) reach the API through
`CHOREGOS_PLUGINS_CONFIG`. The reconcile installs each one in every organisation:

- **idempotent**: a plugin the organisation has (at any version) is left alone, and so is one already
  waiting for its second approval; running it twice changes nothing. Upgrading stays an administrator's
  gesture (ADR 0040);
- **under the same gate as a person's install**: every supply-chain check runs (S43-03), and a default
  plugin the gate sends to a second administrator (a server, a high-risk skill) is NOT installed: it is
  recorded as a request waiting for an administrator's approval (`requested_by` empty, `default` true),
  logged as `plugin.default.pending_approval` and listed by `GET /orgs/{org}/plugin-approvals`;
- **unless the operator pre-approved it** (`preapproved: true` on the entry, S43-09): listing a plugin in
  the deployment's configuration is a decision taken through the deployment's own review, so the gate's
  reasons are recorded in the audit trail (`plugin.default.preapproved`, approved by the deployment
  configuration) and the plugin is installed. The API offers no such flag: a person's install always
  waits for the second administrator;
- **offline first**: a default whose source is not the offline directory is taken from it when it holds a
  version inside the requested range (the image's baked `plugins.lock`, S43-09) — no network at start-up,
  the same content digest the source publishes — and recorded under its declared source, which serves its
  upgrades;
- **one organisation at a time, one plugin at a time**: a plugin that fails (an unreachable source, a
  refused signature) is logged and the others go on; each plugin's writes are a savepoint;
- **safe on every replica**: each organisation is reconciled under a PostgreSQL advisory transaction
  lock, so two replicas starting together take turns, and the second finds everything done.

It runs at API start-up (a background task, so a slow source never holds the readiness probe) and when
an organisation is created. What it installs is recorded `installed_by: null`, `default: true` (the
contract's way of saying "the platform").

`check_configuration` is the start-up check: a configuration that cannot be read, or an offline
directory that does not exist, stops the API with a message naming the setting, rather than a 500 at
the first request.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from choregos_core.plugins import PluginError
from choregos_core.plugins.sources import DefaultPlugin, PluginsConfig, SourceError
from choregos_core.plugins.versions import VersionError, admits
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.models import Organization
from ..errors import ApiError
from ..logging import get_logger
from ..rbac import SYSTEM
from . import plugin_approvals as approvals
from . import plugin_installer as installer
from .plugin_sources import assess_plugin, plugins_config
from .plugin_supply import (
    OFFLINE,
    PluginNotFoundError,
    PluginSupply,
    PluginVersionUnsupportedError,
    plugin_supply,
)

log = get_logger(__name__)

INSTALLED = "installed"
PRESENT = "present"
PENDING = "pending_approval"
FAILED = "failed"


class PluginsConfigurationError(RuntimeError):
    """The deployment's plugin configuration is unusable: the API does not start."""


def check_configuration(settings: Settings | None = None) -> PluginsConfig:
    """Reads `CHOREGOS_PLUGINS_CONFIG` and `CHOREGOS_PLUGINS_OFFLINE` once, at start-up."""
    settings = settings or get_settings()
    try:
        config = plugins_config()
    except SourceError as exc:
        raise PluginsConfigurationError(
            f"CHOREGOS_PLUGINS_CONFIG ({settings.plugins_config or 'unset'}) is invalid: {exc.message}. "
            "Fix the chart's `global.plugins` values; the API does not start with a configuration it "
            "would refuse at every install."
        ) from exc
    if settings.plugins_config and settings.plugins_offline:
        try:
            import yaml

            declared = (yaml.safe_load(Path(settings.plugins_config).read_text(encoding="utf-8")) or {}).get(
                "offline"
            )
        except (OSError, yaml.YAMLError, AttributeError):
            declared = None
        if declared and Path(str(declared)) != Path(settings.plugins_offline):
            raise PluginsConfigurationError(
                f"two offline plugin directories: CHOREGOS_PLUGINS_OFFLINE={settings.plugins_offline} and "
                f"`offline: {declared}` in {settings.plugins_config}. Set one of them."
            )
    if config.offline is not None and not config.offline.is_dir():
        raise PluginsConfigurationError(
            f"the offline plugin directory {config.offline} does not exist in this image "
            "(`global.plugins.offline` or CHOREGOS_PLUGINS_OFFLINE)"
        )
    return config


@dataclass(frozen=True)
class Outcome:
    org: str
    plugin: str
    outcome: str
    detail: str = ""


async def _offline_version(supply: PluginSupply, config: PluginsConfig, wanted: DefaultPlugin) -> str | None:
    """The offline directory's version of `wanted`, when its source is another one and that version is
    inside the requested range; `None` otherwise (the declared source serves it)."""
    if wanted.source == OFFLINE or config.offline is None:
        return None
    try:
        version = await supply.latest(OFFLINE, wanted.name)
    except (SourceError, PluginNotFoundError):
        return None
    if version is None:
        return None
    try:
        return version if wanted.version == "*" or admits(wanted.version, version) else None
    except VersionError:
        return None


def _lock_number(org_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"plugin-defaults:{org_id}".encode()).digest()[:8], "big", signed=True
    )


async def reconcile_org(
    session: AsyncSession, org_id: str, settings: Settings | None = None, config: PluginsConfig | None = None
) -> list[Outcome]:
    """Installs the default plugins this organisation lacks, in `session`'s transaction."""
    settings = settings or get_settings()
    try:
        config = config or plugins_config()
    except SourceError as exc:
        log.error("plugin.default.config_invalid", check=exc.check, detail=exc.message)
        return []
    if not config.default_plugins:
        return []
    if not settings.is_sqlite:
        # Two replicas starting together take turns; the second finds every plugin present.
        await session.execute(text("SELECT pg_advisory_xact_lock(:n)"), {"n": _lock_number(org_id)})
    slug = (await session.execute(select(Organization.slug).where(Organization.id == org_id))).scalar_one()
    supply = plugin_supply(settings)
    outcomes: list[Outcome] = []
    for wanted in config.default_plugins:
        if await installer.installed_row(session, org_id, wanted.name) is not None:
            outcomes.append(Outcome(slug, wanted.name, PRESENT))
            continue
        if await approvals.pending_install(session, org_id, wanted.name) is not None:
            outcomes.append(Outcome(slug, wanted.name, PENDING, "already waiting"))
            continue
        try:
            offline = await _offline_version(supply, config, wanted)
            obtained = (
                supply.obtain(OFFLINE, wanted.name, offline)
                if offline is not None
                else supply.obtain(wanted.source, wanted.name, wanted.version)
            )
            async with obtained as plugin:
                gate = assess_plugin(plugin)
                async with session.begin_nested():
                    if gate.needs_second_approval and wanted.preapproved:
                        await record(session, SYSTEM, "plugin.default.preapproved", org_id=org_id,
                                     target_type="plugin", target_id=plugin.name, version=plugin.version,
                                     digest=plugin.digest, reasons=list(gate.reasons),
                                     approved_by="deployment configuration")  # fmt: skip
                        log.warning(
                            "plugin.default.preapproved",
                            org=slug,
                            plugin=plugin.name,
                            version=plugin.version,
                            reasons=list(gate.reasons),
                        )
                    elif gate.needs_second_approval:
                        await installer.check(session, org_id, plugin, settings, replacing=None)
                        row, _ = await approvals.request(
                            session, org_id, approvals.INSTALL, wanted.source, plugin, gate.reasons, None,
                            default=True,
                        )  # fmt: skip
                        log.warning(
                            "plugin.default.pending_approval",
                            org=slug,
                            plugin=plugin.name,
                            version=plugin.version,
                            approval_id=row.id,
                            reason=row.reason,
                        )
                        outcomes.append(Outcome(slug, plugin.name, PENDING, row.reason))
                        continue
                    await installer.install(
                        session, org_id, plugin, wanted.source, settings, None, default=True
                    )
            log.info(
                "plugin.default.installed",
                org=slug,
                plugin=plugin.name,
                version=plugin.version,
                from_offline=offline is not None,
                preapproved=gate.needs_second_approval,
            )
            outcomes.append(Outcome(slug, wanted.name, INSTALLED, plugin.version))
        except (
            SourceError,
            PluginError,
            PluginNotFoundError,
            PluginVersionUnsupportedError,
            ApiError,
        ) as exc:
            detail = exc.message if isinstance(exc, SourceError) else (getattr(exc, "detail", "") or str(exc))
            log.error(
                "plugin.default.failed", org=slug, plugin=wanted.name, source=wanted.source, detail=detail
            )
            outcomes.append(Outcome(slug, wanted.name, FAILED, str(detail)))
    return outcomes


async def reconcile_all(settings: Settings | None = None) -> list[Outcome]:
    """Every organisation, each in its own transaction (start-up)."""
    from ..db.session import session_scope

    settings = settings or get_settings()
    try:
        config = plugins_config()
    except SourceError as exc:
        log.error("plugin.default.config_invalid", check=exc.check, detail=exc.message)
        return []
    if not config.default_plugins:
        return []
    async with session_scope(orgs="*") as session:
        org_ids = list((await session.execute(select(Organization.id).order_by(Organization.slug))).scalars())
    outcomes: list[Outcome] = []
    for org_id in org_ids:
        async with session_scope(orgs="*") as session:
            outcomes += await reconcile_org(session, org_id, settings, config)
    pending = [o for o in outcomes if o.outcome == PENDING]
    log.info(
        "plugin.default.reconciled",
        organisations=len(org_ids),
        installed=sum(o.outcome == INSTALLED for o in outcomes),
        pending_approval=[f"{o.org}/{o.plugin}" for o in pending],
        failed=[f"{o.org}/{o.plugin}" for o in outcomes if o.outcome == FAILED],
    )
    return outcomes


async def reconcile_in_background(settings: Settings) -> None:
    """The start-up task: never lets an error escape (it would only be printed when the task is
    collected), and never holds the readiness probe."""
    try:
        await reconcile_all(settings)
    except Exception:
        log.exception("plugin.default.reconcile_failed")


__all__ = [
    "FAILED",
    "INSTALLED",
    "PENDING",
    "PRESENT",
    "Outcome",
    "PluginsConfigurationError",
    "check_configuration",
    "reconcile_all",
    "reconcile_in_background",
    "reconcile_org",
]
