# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: discovered, installed from the console, claimed (S40-04, ADR 0073).

Nobody types an installation id. The platform learns installations by polling the App
(`GET /app/installations`), from the Setup URL redirect after an install, and from webhooks. A
Choregos organisation CLAIMS an installation, and a token for `owner/repo` is only minted through an
installation its organisation claimed. On an instance with one organisation (the community edition,
ADR 0024) every installation is claimed implicitly; with several, the claim needs proof that the
person installing manages the installation (GitHub user authorization).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from choregos_adapters import fakes_enabled
from choregos_adapters.errors import UpstreamError
from choregos_adapters.fakes.github_app import FakeGitHubApp
from choregos_adapters.github import GitHubApp, GitHubAppPort
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import GitHubInstallation, Organization
from ..db.session import en_portee_de_plateforme
from ..errors import conflict, forbidden, unprocessable
from ..logging import get_logger

logger = get_logger("choregos.github_app")

#: A sync older than this is redone when the status is read.
STALE_AFTER_S = 600
STATE_TTL_S = 900
INSTALLATION_ACTIONS = {"created", "unsuspend", "suspend", "new_permissions_accepted"}
GITHUB_WEB = "https://github.com"
#: Connector types whose `repo` is served by the App.
GITHUB_TYPES = {("tracker", "github-issues"), ("scm", "github")}

_real: dict[tuple[str, str, str, str], GitHubApp] = {}


def get_github_app() -> GitHubAppPort | None:
    """The deployment's App, or `None` when it has none. Fakes: the scriptable `FakeGitHubApp`,
    absent until a test sets `configured`."""
    if fakes_enabled():
        fake = FakeGitHubApp.shared()
        return fake if fake.configured else None
    settings = get_settings()
    key = (
        settings.github_app_id,
        settings.github_app_private_key,
        settings.github_app_client_id,
        settings.github_app_client_secret,
    )
    if not key[0] or not key[1]:
        return None
    if key not in _real:
        _real.clear()  # one App per process: a changed credential replaces the client
        _real[key] = GitHubApp(app_id=key[0], private_key=key[1], client_id=key[2], client_secret=key[3])
    return _real[key]


def owner_of(repo: str) -> str | None:
    """The owner of an `owner/repo` string, `None` when it is not one."""
    owner, _, name = (repo or "").strip().partition("/")
    return owner if owner and name and "/" not in name else None


# ───────────────────────────── signed state ─────────────────────────────


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    key = get_settings().session_secret.encode()
    return _b64(hmac.new(key, f"github-app-state:{payload}".encode(), hashlib.sha256).digest())


def safe_return_to(return_to: str) -> str:
    """A path of the console: `/…`, never `//host` or a URL (no open redirect)."""
    if not return_to.startswith("/") or return_to.startswith("//") or "\\" in return_to:
        raise unprocessable("`return_to` must be a path of the console, starting with a single `/`")
    return return_to


def sign_state(*, org: str, project: str | None, user: str, return_to: str, now: float | None = None) -> str:
    claims = {
        "org": org,
        "project": project,
        "user": user,
        "return_to": safe_return_to(return_to),
        "exp": int((now if now is not None else time.time()) + STATE_TTL_S),
        "nonce": secrets.token_hex(8),
    }
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_sign(payload)}"


def verify_state(state: str, *, now: float | None = None) -> dict[str, Any]:
    """The claims of a state we signed and that has not expired; otherwise 403."""
    payload, _, signature = state.partition(".")
    if not payload or not signature or not hmac.compare_digest(signature, _sign(payload)):
        raise forbidden("the GitHub installation state is not valid: start again from the console")
    try:
        claims = json.loads(_unb64(payload))
    except ValueError as exc:
        raise forbidden("the GitHub installation state is not valid: start again from the console") from exc
    if int(claims.get("exp", 0)) < (now if now is not None else time.time()):
        raise forbidden("the GitHub installation state expired: start again from the console")
    return dict(claims)


# ───────────────────────────── installations ─────────────────────────────


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


async def organisations(session: AsyncSession) -> list[Organization]:
    async with en_portee_de_plateforme(session):
        return list((await session.execute(select(Organization).order_by(Organization.created_at))).scalars())


async def implicit_claim_enabled(session: AsyncSession) -> bool:
    """Exactly one organisation on the instance: nobody else to steal an installation from."""
    async with en_portee_de_plateforme(session):
        return (await session.execute(select(func.count()).select_from(Organization))).scalar_one() == 1


async def upsert_installation(
    session: AsyncSession, raw: dict[str, Any], *, now: datetime | None = None
) -> GitHubInstallation:
    """Insert or refresh one installation from GitHub's JSON (REST or webhook payload)."""
    account = raw.get("account") or {}
    login = str(account.get("login") or "")
    now = now or datetime.now(UTC)
    row = await session.get(GitHubInstallation, int(raw["id"]))
    if row is None:
        row = GitHubInstallation(id=int(raw["id"]))
        session.add(row)
    row.account_login = login
    row.account_key = login.lower()
    row.account_id = int(account.get("id") or 0)
    row.account_type = str(account.get("type") or "Organization")
    row.app_id = str(raw.get("app_id") or "")
    row.repository_selection = str(raw.get("repository_selection") or "selected")
    row.suspended_at = _when(raw.get("suspended_at"))
    row.synced_at = now
    await session.flush()
    return row


async def claim_implicitly(session: AsyncSession) -> int:
    """On a single-organisation instance, the only organisation claims every unclaimed installation."""
    orgs = await organisations(session)
    if len(orgs) != 1:
        return 0
    async with en_portee_de_plateforme(session):
        rows = (
            (await session.execute(select(GitHubInstallation).where(GitHubInstallation.org_id.is_(None))))
            .scalars()
            .all()
        )
        for row in rows:
            row.org_id = orgs[0].id
            row.claimed_by = "system:implicit"
            row.claimed_at = datetime.now(UTC)
        await session.flush()
    return len(rows)


async def sync_installations(session: AsyncSession, app: GitHubAppPort) -> int:
    """Poll GitHub, upsert every installation, drop the ones that disappeared, claim implicitly."""
    remote = await app.list_installations()
    now = datetime.now(UTC)
    async with en_portee_de_plateforme(session):
        for raw in remote:
            await upsert_installation(session, raw, now=now)
        seen = {int(r["id"]) for r in remote}
        for row in (await session.execute(select(GitHubInstallation))).scalars().all():
            if row.id not in seen:
                await session.delete(row)
        await session.flush()
    await claim_implicitly(session)
    return len(remote)


async def last_sync(session: AsyncSession) -> datetime | None:
    async with en_portee_de_plateforme(session):
        value = (await session.execute(select(func.max(GitHubInstallation.synced_at)))).scalar_one()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


async def sync_if_stale(session: AsyncSession, app: GitHubAppPort | None) -> None:
    """The lazy sync of a read: a GitHub outage never fails the read, it serves what is stored."""
    if app is None:
        return
    synced = await last_sync(session)
    if synced is not None and (datetime.now(UTC) - synced).total_seconds() < STALE_AFTER_S:
        return
    try:
        await sync_installations(session, app)
    except UpstreamError as exc:
        logger.warning("github installations sync failed", error=str(exc))


async def claimed_installations(
    session: AsyncSession, org_id: str, *, usable_only: bool = False
) -> list[GitHubInstallation]:
    async with en_portee_de_plateforme(session):
        query = select(GitHubInstallation).where(GitHubInstallation.org_id == org_id)
        if usable_only:
            query = query.where(GitHubInstallation.suspended_at.is_(None))
        return list((await session.execute(query.order_by(GitHubInstallation.account_key))).scalars().all())


async def claimed_installation_for(
    session: AsyncSession, org_id: str, owner: str
) -> GitHubInstallation | None:
    """The installation THIS organisation claimed on `owner`'s GitHub account, if it is usable.

    The isolation boundary (ADR 0073): a token for `owner/repo` is minted through no other."""
    async with en_portee_de_plateforme(session):
        return (
            await session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.org_id == org_id,
                    GitHubInstallation.account_key == owner.lower(),
                    GitHubInstallation.suspended_at.is_(None),
                )
            )
        ).scalar_one_or_none()


async def installation_for_owner(
    session: AsyncSession, org_id: str, owner: str, app: GitHubAppPort | None
) -> GitHubInstallation | None:
    """`claimed_installation_for`, after one sync when nothing is claimed yet: on a single-organisation
    instance a freshly installed App is claimed at that sync, with no gesture."""
    found = await claimed_installation_for(session, org_id, owner)
    if found is not None or app is None or not await implicit_claim_enabled(session):
        return found
    try:
        await sync_installations(session, app)
    except UpstreamError as exc:
        logger.warning("github installations sync failed", error=str(exc))
        return None
    return await claimed_installation_for(session, org_id, owner)


def install_page(slug: str | None) -> str | None:
    return f"{GITHUB_WEB}/apps/{slug}/installations/new" if slug else None


def settings_url(row: GitHubInstallation) -> str:
    if row.account_type == "Organization":
        return f"{GITHUB_WEB}/organizations/{row.account_login}/settings/installations/{row.id}"
    return f"{GITHUB_WEB}/settings/installations/{row.id}"


async def claim(
    session: AsyncSession,
    *,
    installation_id: int,
    org: Organization,
    user_email: str,
    app: GitHubAppPort,
    oauth_code: str | None,
) -> GitHubInstallation:
    """Claim an installation for an organisation.

    409 when another organisation owns it. On several organisations, the person must prove they
    manage the installation: the code GitHub sends back is exchanged for their token, and
    `GET /user/installations` must contain the id. GitHub's own docs warn that the callback's
    `installation_id` can be forged, so it is never trusted alone there."""
    async with en_portee_de_plateforme(session):
        row = await session.get(GitHubInstallation, installation_id)
    if row is not None and row.org_id not in (None, org.id):
        raise conflict(f"installation {installation_id} is already claimed by another organisation")
    implicit = await implicit_claim_enabled(session)
    if not implicit:
        if not app.oauth_enabled:
            raise forbidden(
                "claiming a GitHub installation needs proof that you manage it: set the App's OAuth client "
                "(CHOREGOS_GITHUB_APP_CLIENT_ID and CHOREGOS_GITHUB_APP_CLIENT_SECRET), and tick "
                "'Request user authorization (OAuth) during installation' on the App"
            )
        if not oauth_code:
            raise forbidden("GitHub did not send an authorization code: tick 'Request user authorization'")
        try:
            allowed = await app.user_installation_ids(oauth_code)
        except UpstreamError as exc:
            raise forbidden(f"GitHub refused the authorization code: {exc}") from exc
        if installation_id not in allowed:
            raise forbidden(f"you do not manage GitHub installation {installation_id}")
    async with en_portee_de_plateforme(session):
        if row is None:
            remote = {int(r["id"]): r for r in await app.list_installations()}
            if installation_id not in remote:
                raise unprocessable(f"GitHub does not know installation {installation_id} for this App")
            row = await upsert_installation(session, remote[installation_id])
        row.org_id = org.id
        row.claimed_by = user_email
        row.claimed_at = datetime.now(UTC)
        await session.flush()
    return row


# ───────────────────────────── webhooks ─────────────────────────────


async def apply_installation_event(session: AsyncSession, event: str, payload: dict[str, Any]) -> bool:
    """`installation` and `installation_repositories` webhooks keep the table in step when GitHub can
    reach the deployment. Returns False for an action it does not act on."""
    raw = payload.get("installation")
    if not isinstance(raw, dict) or "id" not in raw:
        return False
    action = str(payload.get("action") or "")
    async with en_portee_de_plateforme(session):
        if event == "installation" and action == "deleted":
            row = await session.get(GitHubInstallation, int(raw["id"]))
            if row is not None:
                await session.delete(row)
            return True
        if event == "installation" and action in {
            "created",
            "unsuspend",
            "suspend",
            "new_permissions_accepted",
        }:
            if action == "suspend" and not raw.get("suspended_at"):
                raw = {**raw, "suspended_at": datetime.now(UTC).isoformat()}
            if action == "unsuspend":
                raw = {**raw, "suspended_at": None}
            await upsert_installation(session, raw)
            if action == "created":
                await claim_implicitly(session)
            return True
        if event == "installation_repositories":
            await upsert_installation(session, raw)
            return True
    return False


# ───────────────────────────── injection into connector config ─────────────────────────────


async def pin_installation(
    session: AsyncSession, org_id: str, kind: str, type_: str, config: dict[str, Any]
) -> dict[str, Any]:
    """The connector's config with the installation THIS organisation claimed injected.

    Never stored in `connectors.config` and never shown (ADR 0073). Without a claimed installation
    the config is returned as is: discovery by repository, today's behaviour, which the callers that
    must refuse (`put_connector`, a multi-organisation instance) check beforehand."""
    if (kind, type_) not in GITHUB_TYPES:
        return config
    owner = owner_of(str(config.get("repo") or ""))
    clean = {k: v for k, v in config.items() if k != "installation_id"}
    if owner is None:
        return clean
    found = await claimed_installation_for(session, org_id, owner)
    return {**clean, "installation_id": found.id} if found is not None else clean


async def checked_config(
    session: AsyncSession, org_id: str, kind: str, type_: str, config: dict[str, Any]
) -> dict[str, Any]:
    """The config a GitHub connector may be saved with (422 otherwise).

    A legacy `installation_id` is dropped: it is derived, and a typed one could name someone else's
    installation. When the deployment has an App, the repository's owner must have an installation
    THIS organisation claimed. A connector on another host (`api_url`) or one that names no
    repository is left to its own validation."""
    clean = {k: v for k, v in config.items() if k != "installation_id"}
    if (kind, type_) not in GITHUB_TYPES or clean.get("api_url"):
        return clean
    owner = owner_of(str(clean.get("repo") or ""))
    app = get_github_app()
    if owner is None or app is None:
        return clean
    if await installation_for_owner(session, org_id, owner, app) is not None:
        return clean
    link = None
    with contextlib.suppress(UpstreamError):
        link = install_page((await app.app_info()).get("slug"))
    raise unprocessable(
        f"The Choregos GitHub App is not installed on `{owner}` for this organisation. "
        + (f"Install it: {link}" if link else "Install it from Settings, Connections.")
    )


async def pin_for_runtime(
    session: AsyncSession, org_id: str, kind: str, type_: str, config: dict[str, Any]
) -> dict[str, Any]:
    """`pin_installation` for the orchestrator, which builds adapters for a run.

    On an instance with several organisations a GitHub connector whose owner has no installation
    claimed by its organisation does not fall back to discovery (any organisation could then borrow
    another's installation): it fails, naming the way out."""
    pinned = await pin_installation(session, org_id, kind, type_, config)
    if (kind, type_) not in GITHUB_TYPES or "installation_id" in pinned or pinned.get("api_url"):
        return pinned
    owner = owner_of(str(pinned.get("repo") or ""))
    if owner is None or get_github_app() is None or await implicit_claim_enabled(session):
        return pinned
    from choregos_adapters.errors import ConfigurationError

    raise ConfigurationError(
        f"The Choregos GitHub App is not installed on `{owner}` for this organisation: "
        "install it from Settings, Connections, then retry."
    )
