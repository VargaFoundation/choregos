# SPDX-License-Identifier: Apache-2.0
"""Checking a knowledge source before it is stored: connector, configuration, egress (S33-04)."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from choregos_adapters.registry import fakes_enabled
from choregos_core.outbound_proxy import OutboundProxyError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    KnowledgeBase,
    KnowledgeSource,
    Organization,
    OrgConnector,
)
from ...deps import Me
from ...errors import not_found, unprocessable
from ...knowledge.sync import CONNECTED_KINDS, OPTIONALLY_CONNECTED, SOURCE_CONNECTOR_TYPES
from ...secret_store import resolved_configuration
from ...services.egress import egress_scope, ensure_or_409, knowledge_source_targets, targets_of_connector
from ...services.outbound_proxy import proxy_scope
from ...services.platform_network import check_platform_host
from ...temporal import get_temporal, knowledge_sync_id

_HTTPS = ("https://",)


async def _documents_connector(
    session: AsyncSession, organisation: Organization, name: str, source_kind: str
) -> OrgConnector:
    """The `documents` connector a source names, or the reason it is not one (422).

    Connector names are lowercase, so the lookup ignores case and says what the name should be;
    a connector of another kind or type is named for what it is rather than "not found".
    """
    rows = (
        (await session.execute(select(OrgConnector).where(OrgConnector.org_id == organisation.id)))
        .scalars()
        .all()
    )
    exact = [c for c in rows if c.name == name]
    folded = [c for c in rows if c.name.lower() == name.strip().lower()]
    match = (exact + folded)[0] if exact or folded else None
    if match is not None and match.kind != "documents":
        raise unprocessable(
            f"no `documents` connector `{name}` in {organisation.slug}: "
            f"`{match.name}` is a {match.kind}/{match.type} connector, not a documents one"
        )
    if match is not None and match.name != name:
        raise unprocessable(
            f"no `documents` connector `{name}` in {organisation.slug} "
            f'(names are lowercase: did you mean "{match.name}"?)'
        )
    if match is not None:
        allowed = SOURCE_CONNECTOR_TYPES.get(source_kind, frozenset())
        if match.type not in allowed:
            raise unprocessable(
                f"`{name}` is a documents/{match.type} connector: "
                f"a {source_kind} source needs {' or '.join(sorted(allowed))}"
            )
        return match
    names = sorted(c.name for c in rows if c.kind == "documents")
    listing = (
        f"documents connectors here: {', '.join(names)}"
        if names
        else "none: an org admin declares one in /admin/connectors"
    )
    raise unprocessable(f"no `documents` connector `{name}` in {organisation.slug} ({listing})")


def _sharepoint_config(config: dict[str, object]) -> dict[str, object]:
    if not (config.get("site_id") or config.get("drive_id")):
        raise unprocessable("a SharePoint or OneDrive source names a `site_id` or a `drive_id`")
    return {k: str(v) for k, v in config.items() if k in {"site_id", "drive_id", "folder"} and v}


_SPACE_KEY = re.compile(r"^[A-Z0-9~][A-Za-z0-9_~-]{0,254}$")


def _confluence_config(config: dict[str, object]) -> dict[str, object]:
    space = str(config.get("space_key") or "")
    if not _SPACE_KEY.match(space):
        raise unprocessable("a Confluence source names a `space_key` (letters and digits, e.g. ENG)")
    page = config.get("page_id")
    if page not in (None, "") and not re.fullmatch(r"\d{1,20}", str(page)):
        raise unprocessable("`page_id` is the number in the page's URL (/pages/<id>/…)")
    ids = config.get("page_ids")
    if ids not in (None, []):
        if page not in (None, ""):
            raise unprocessable("name `page_id` or `page_ids`, not both")
        if (
            not isinstance(ids, list)
            or not len(ids) > 0
            or len(ids) > 50
            or not all(isinstance(x, str | int) and re.fullmatch(r"\d{1,20}", str(x)) for x in ids)
        ):
            raise unprocessable("`page_ids` is a list of at most 50 page numbers (/pages/<id>/…)")
    for flag in ("include_root", "include_attachments", "descendants"):
        if flag in config and not isinstance(config[flag], bool):
            raise unprocessable(f"`{flag}` is true or false")
    if config.get("descendants") is False and config.get("include_root") is False:
        raise unprocessable("`descendants` false with `include_root` false leaves no page")
    labels = config.get("labels") or []
    if (
        not isinstance(labels, list)
        or len(labels) > 20
        or not all(isinstance(x, str) and 0 < len(x) <= 255 for x in labels)
    ):
        raise unprocessable("`labels` is a list of at most 20 labels")
    out: dict[str, object] = {"space_key": space}
    if page not in (None, ""):
        out["page_id"] = str(page)
    if isinstance(ids, list) and ids:
        out["page_ids"] = list(dict.fromkeys(str(x) for x in ids))
    if config.get("descendants") is False:
        out["descendants"] = False  # absent means true: a stored config reads as before
    out["include_root"] = bool(config.get("include_root", True))
    out["include_attachments"] = bool(config.get("include_attachments", False))
    if labels:
        out["labels"] = labels
    return out


def _web_config(config: dict[str, object]) -> dict[str, object]:
    url = _https_url(config, "web")
    exclude = config.get("exclude") or []
    if (
        not isinstance(exclude, list)
        or len(exclude) > 50
        or not all(isinstance(p, str) and p.startswith("/") and len(p) <= 200 for p in exclude)
    ):
        raise unprocessable("`exclude` is a list of at most 50 path globs, each starting with `/`")
    checked: dict[str, object] = {
        "url": url,
        "max_depth": max(0, min(int(str(config.get("max_depth", 2))), 5)),
        "max_pages": max(1, min(int(str(config.get("max_pages", 50))), 500)),
    }
    if exclude:
        checked["exclude"] = exclude
    headers = config.get("headers") or {}
    if not isinstance(headers, dict):
        raise unprocessable("`headers` maps header names to values")
    if headers:
        from choregos_adapters.documents.base import SourceRefusedError
        from choregos_adapters.documents.web import plain_headers

        try:
            checked["headers"] = plain_headers({str(k): v for k, v in headers.items()})
        except SourceRefusedError as refused:
            raise unprocessable(str(refused)) from refused
    if "include_documents" in config:
        if not isinstance(config["include_documents"], bool):
            raise unprocessable("`include_documents` is true or false")
        if config["include_documents"]:
            checked["include_documents"] = True
    return checked


def _git_config(config: dict[str, object]) -> dict[str, object]:
    url = _https_url(config, "git")
    ref = str(config.get("ref") or "main")
    if ref.startswith("-"):
        raise unprocessable(f"not a git ref: `{ref}`")
    paths = config.get("paths") or ["*.md"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise unprocessable("`paths` is a list of glob patterns")
    return {"url": url, "ref": ref, "paths": paths}


def _https_url(config: dict[str, object], kind: str) -> str:
    url = str(config.get("url") or "")
    if not url.startswith(_HTTPS):
        raise unprocessable(f"a {kind} source is reached over https: `{url}`")
    return url


#: One validator per source kind: the configuration it keeps, or the reason it is refused.
_SOURCE_VALIDATORS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "sharepoint": _sharepoint_config,
    "confluence": _confluence_config,
    "web": _web_config,
    "git": _git_config,
}

_CONNECTED_WHAT = {
    "sharepoint": "SharePoint or OneDrive",
    "confluence": "Confluence",
    "git": "private git",
    "web": "web site behind a login",
}


async def _checked_source_config(
    session: AsyncSession,
    organisation: Organization,
    kind: str,
    connector: str | None,
    config: dict[str, object],
) -> dict[str, object]:
    """What a source may be: refused here, with its reason, rather than at its first sync."""
    if kind in CONNECTED_KINDS or (kind in OPTIONALLY_CONNECTED and connector):
        if not connector:
            raise unprocessable(f"a {_CONNECTED_WHAT.get(kind, kind)} source names its `documents` connector")
        found = await _documents_connector(session, organisation, connector, kind)
    checked = _SOURCE_VALIDATORS[kind](config)
    if kind == "web" and connector:
        # The credential is pinned to its host: a site elsewhere is refused now, not at its first sync.
        host = (urlsplit(str(checked["url"])).hostname or "").lower()
        pinned = str((found.config or {}).get("host") or "").lower()
        if host != pinned:
            raise unprocessable(
                f"`{checked['url']}` is not on `{pinned}`: the connector `{connector}`'s credential "
                "is never sent to another host"
            )
    if kind in {"web", "git"}:
        # The organisation's network bounds (ADR 0070): a site its ceiling denies is not crawled, and
        # the refusal comes now (422, nothing an opening could fix), before the egress gate's 409.
        await check_platform_host(session, organisation.id, str(checked["url"]), f"{kind} source")
    return checked


async def _reachable_start(
    session: AsyncSession,
    organisation: Organization,
    kind: str,
    config: dict[str, object],
    connector: str | None = None,
) -> None:
    """A web source's start URL answers, checked AFTER the egress gate (which may just have allowed
    its host) and INSIDE the organisation's policy: the guarded client fails closed without one
    (S39-28, seen on the dev tenant in `enforce`)."""
    if kind != "web" or fakes_enabled():
        return
    exclude = config.get("exclude") or []
    assert isinstance(exclude, list)
    headers = dict(config.get("headers") or {})  # type: ignore[call-overload]
    if connector:
        # A site behind a login answers its login page to an anonymous request: ask as the source will.
        from choregos_adapters.documents.base import SourceRefusedError
        from choregos_adapters.documents.web import WebDocumentsConnector

        found = await _documents_connector(session, organisation, connector, kind)
        resolved = await resolved_configuration(
            session, organisation.id, found.kind, found.type, found.config, found.secret_refs
        )
        try:
            headers |= WebDocumentsConnector(resolved).headers()
        except SourceRefusedError as refused:
            raise unprocessable(str(refused)) from refused
    try:
        async with (
            egress_scope(session, organisation, "api", "knowledge-source:web"),
            proxy_scope(session, organisation.id, ("knowledge_web",)),
        ):
            await _check_web_start(str(config["url"]), exclude, headers)
    except OutboundProxyError as unusable:
        raise unprocessable(str(unusable)) from unusable


async def _check_web_start(url: str, exclude: list[str], headers: dict[str, str] | None = None) -> None:
    """A start URL that redirects outside the allow-list yields no page: say so now, with the target."""
    from choregos_adapters.documents import web
    from choregos_adapters.documents.base import SourceRefusedError

    async def resolver(host: str) -> list[str]:
        return await web.resolve(host)

    try:
        crawl = web.WebCrawlSource(url=url, exclude=exclude, resolver=resolver, headers=headers)
        try:
            await crawl.check_start()
        finally:
            await crawl.aclose()
    except SourceRefusedError as exc:
        raise unprocessable(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise unprocessable(f"`{url}` is not reachable: {exc}") from exc


async def _egress_gate(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    kind: str,
    connector: str | None,
    config: dict[str, object],
    name: str,
    allow_egress: bool,
) -> None:
    """A source's hosts must be in the organisation's egress allowlist (S39-22): the modal's 409,
    or, for an administrator who says so, opened in this same transaction. A connected source
    (`sharepoint`, `confluence`) calls the hosts its connector declares."""
    targets = knowledge_source_targets(kind, dict(config))
    if connector and kind in CONNECTED_KINDS:
        found = (
            await session.execute(
                select(OrgConnector).where(
                    OrgConnector.org_id == organisation.id, OrgConnector.name == connector
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            targets += targets_of_connector(found.kind, found.type, dict(found.config or {}))
    await ensure_or_409(
        session,
        principal,
        organisation,
        targets,
        allow_egress=allow_egress,
        origin=f"knowledge-source:{name}",
        reason=f"the {kind} knowledge source `{name}`",
    )


async def _source(session: AsyncSession, base: KnowledgeBase, source_id: str) -> KnowledgeSource:
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.knowledge_base_id == base.id, KnowledgeSource.id == source_id
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise not_found("Knowledge source", source_id)
    return source


def _checked_refresh(mode: str, cron: str | None, timezone: str) -> tuple[str | None, str]:
    """The refresh of a source: refused here, with its reason (a bad cron, a gap under 5 minutes, an
    unknown time zone). `interval` and `manual` keep no cron."""
    from ...knowledge.refresh import check_cron

    if mode != "cron":
        return None, timezone if _zone_known(timezone) else _refuse_zone(timezone)
    if not cron:
        raise unprocessable("a `cron` refresh needs a `refresh_cron`")
    try:
        check_cron(cron, timezone)
    except ValueError as error:
        raise unprocessable(str(error)) from error
    return " ".join(cron.split()), timezone


def _zone_known(name: str) -> bool:
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def _refuse_zone(name: str) -> str:
    raise unprocessable(f"unknown time zone `{name}`")


async def _start_sync_loop(org: str, source: KnowledgeSource) -> None:
    await get_temporal().start_knowledge_sync(
        knowledge_sync_id(source.id),
        {"org": org, "source_id": source.id, "interval_seconds": source.interval_minutes * 60},
    )
