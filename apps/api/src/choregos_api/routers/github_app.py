# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: discovered, installed from the console, claimed (S40-04, ADR 0073).

Nobody types an installation id: `GET /orgs/{org}/github` says what the App sees, the install link
sends the person to github.com and back through the App's Setup URL (`GET /github/setup`), and the
installation is claimed for the organisation that started the flow.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from choregos_adapters.errors import UpstreamError
from choregos_adapters.github import GitHubAppPort
from fastapi import APIRouter, Path, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import GitHubInstallation, Organization
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found, upstream
from ..rbac import Permission, Principal
from ..schemas import (
    GitHubAppStatus,
    GitHubInstallationDto,
    GitHubInstallLink,
    GitHubInstallLinkRequest,
    GitHubRepository,
    GitHubRepositoryPage,
)
from ..schemas.base import PageMeta
from ..services import github_app as svc

router = APIRouter(tags=["connectors"])

PAGE_SIZE = 50
OrgSlug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    found = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if found is None:
        raise not_found("Organisation", slug)
    return found


def _may_manage(principal: Principal, org: str) -> bool:
    """`connector:write` on the organisation, or on some project of it."""
    if principal.can(Permission.CONNECTOR_WRITE, org):
        return True
    prefix = f"{org}/"
    return any(
        principal.can(Permission.CONNECTOR_WRITE, org, key.removeprefix(prefix))
        for key in principal.project_roles
        if key.startswith(prefix)
    )


async def _authorised(session: AsyncSession, principal: Principal, slug: str) -> Organization:
    organisation = await _organisation(session, slug)
    if not _may_manage(principal, slug):
        raise forbidden(f"managing the GitHub App of {slug} needs the connector right on one of its projects")
    return organisation


async def _status(
    session: AsyncSession, organisation: Organization, app: GitHubAppPort | None
) -> GitHubAppStatus:
    if app is None:
        return GitHubAppStatus(configured=False, multi_org_claim="unavailable", installations=[])
    if await svc.implicit_claim_enabled(session):
        mode = "implicit"
    else:
        mode = "oauth" if app.oauth_enabled else "unavailable"
    slug = name = None
    try:
        info = await app.app_info()
        slug, name = info.get("slug"), info.get("name")
    except UpstreamError:
        pass  # the status still lists what is stored; the link needs the slug and is left out
    rows = await svc.claimed_installations(session, organisation.id)
    return GitHubAppStatus(
        configured=True,
        app_slug=slug,
        app_name=name,
        install_url=svc.install_page(slug),
        multi_org_claim=mode,
        installations=[
            GitHubInstallationDto(
                id=row.id,
                account_login=row.account_login,
                account_type=row.account_type,
                repository_selection=row.repository_selection,
                suspended=row.suspended_at is not None,
                claimed=True,
                settings_url=svc.settings_url(row),
            )
            for row in rows
        ],
    )


@router.get("/orgs/{org}/github", response_model=GitHubAppStatus, operation_id="getGitHubAppStatus")
async def get_github_app_status(org: OrgSlug, principal: Me, session: Db) -> GitHubAppStatus:
    organisation = await _authorised(session, principal, org)
    app = svc.get_github_app()
    await svc.sync_if_stale(session, app)
    return await _status(session, organisation, app)


@router.post(
    "/orgs/{org}/github/refresh", response_model=GitHubAppStatus, operation_id="refreshGitHubInstallations"
)
async def refresh_github_installations(org: OrgSlug, principal: Me, session: Db) -> GitHubAppStatus:
    organisation = await _authorised(session, principal, org)
    app = svc.get_github_app()
    if app is not None:
        try:
            await svc.sync_installations(session, app)
        except UpstreamError as exc:
            raise upstream("github", str(exc)) from exc
    return await _status(session, organisation, app)


@router.get(
    "/orgs/{org}/github/repositories",
    response_model=GitHubRepositoryPage,
    operation_id="listGitHubRepositories",
)
async def list_github_repositories(
    org: OrgSlug,
    principal: Me,
    session: Db,
    q: Annotated[str | None, Query()] = None,
    installation: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> GitHubRepositoryPage:
    organisation = await _authorised(session, principal, org)
    app = svc.get_github_app()
    if app is None:
        return GitHubRepositoryPage(items=[], meta=PageMeta())
    await svc.sync_if_stale(session, app)
    rows = await svc.claimed_installations(session, organisation.id, usable_only=True)
    if installation is not None:
        rows = [row for row in rows if row.id == installation]  # an id the org did not claim: nothing
    found: list[GitHubRepository] = []
    for row in rows:
        try:
            repos = await app.installation_repositories(row.id)
        except UpstreamError as exc:
            raise upstream("github", str(exc)) from exc
        found.extend(
            GitHubRepository(
                full_name=str(r["full_name"]),
                private=bool(r.get("private", True)),
                default_branch=str(r.get("default_branch") or "main"),
                installation_id=row.id,
            )
            for r in repos
        )
    needle = (q or "").strip().lower()
    if needle:
        found = [r for r in found if needle in r.full_name.lower()]
    found.sort(key=lambda r: r.full_name.lower())
    start = (page - 1) * PAGE_SIZE
    chunk = found[start : start + PAGE_SIZE]
    more = start + PAGE_SIZE < len(found)
    return GitHubRepositoryPage(
        items=chunk, meta=PageMeta(next_cursor=str(page + 1) if more else None, has_more=more)
    )


@router.post(
    "/orgs/{org}/github/install-link",
    response_model=GitHubInstallLink,
    operation_id="createGitHubInstallLink",
)
async def create_github_install_link(
    org: OrgSlug, body: GitHubInstallLinkRequest, principal: Me, session: Db
) -> GitHubInstallLink:
    await _authorised(session, principal, org)
    app = svc.get_github_app()
    if app is None:
        raise conflict("this deployment has no GitHub App (CHOREGOS_GITHUB_APP_ID and its private key)")
    try:
        info = await app.app_info()
    except UpstreamError as exc:
        raise upstream("github", str(exc)) from exc
    page = svc.install_page(info.get("slug"))
    if page is None:
        raise conflict("GitHub did not name the App: its slug is unknown")
    state = svc.sign_state(org=org, project=body.project, user=principal.email, return_to=body.return_to)
    return GitHubInstallLink(url=f"{page}?{urlencode({'state': state})}")


def _with_outcome(return_to: str, outcome: str) -> str:
    parts = urlsplit(return_to)
    query = [*parse_qsl(parts.query), ("github", outcome)]
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


@router.get("/github/setup", operation_id="githubAppSetup", status_code=302, response_class=RedirectResponse)
async def github_app_setup(
    principal: Me,
    session: Db,
    installation_id: Annotated[int, Query()],
    state: Annotated[str, Query()],
    setup_action: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """The App's Setup URL: GitHub sends the browser back here after an install or an update."""
    claims = svc.verify_state(state)
    if claims.get("user") != principal.email:
        raise forbidden("this installation was started by another person")
    organisation = await _authorised(session, principal, str(claims["org"]))
    app = svc.get_github_app()
    if app is None:
        raise conflict("this deployment has no GitHub App")
    try:
        row: GitHubInstallation = await svc.claim(
            session,
            installation_id=installation_id,
            org=organisation,
            user_email=principal.email,
            app=app,
            oauth_code=code,
        )
    except UpstreamError as exc:
        raise upstream("github", str(exc)) from exc
    await record(
        session,
        principal,
        "github.installation.claim",
        org_id=organisation.id,
        target_type="github_installation",
        target_id=str(row.id),
        account=row.account_login,
        setup_action=setup_action,
    )
    return RedirectResponse(_with_outcome(str(claims["return_to"]), "claimed"), status_code=302)
