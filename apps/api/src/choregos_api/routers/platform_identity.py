# SPDX-License-Identifier: Apache-2.0
"""How people sign in to this instance, read-only.

The instance-level sign-in configuration (OIDC, the MCP door's OAuth, the session, the bootstrap)
lives in the deployment's settings, not in the database. This route shows it to the platform
administrator so a console can answer "what is this instance configured with?" without shell access.
Secrets are never returned: a secret is reported as `set` or `missing`.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import Settings
from ..deps import Config, Db, Me, exiger_admin_de_plateforme
from ..mcp.oauth import emetteur
from ..schemas import PlatformIdentity, PlatformIdentityRow
from ..tenant_sign_in import has_resolver

router = APIRouter(tags=["admin"])

NOT_CONFIGURED = "not configured"


def _rows(settings: Settings) -> list[PlatformIdentityRow]:
    explicit = settings.model_fields_set

    def src(field: str) -> str:
        return "settings" if field in explicit else "default"

    def row(key: str, label: str, value: str, source: str) -> PlatformIdentityRow:
        return PlatformIdentityRow(key=key, label=label, value=value, source=source)

    configured = "oidc_issuer" in explicit
    issuer = settings.oidc_issuer.rstrip("/")
    rows = [
        row(
            "oidc.issuer",
            "OIDC issuer",
            settings.oidc_issuer if configured else NOT_CONFIGURED,
            src("oidc_issuer"),
        ),
        row(
            "oidc.client_id",
            "OIDC client id",
            settings.oidc_client_id if configured else NOT_CONFIGURED,
            src("oidc_client_id"),
        ),
        row(
            "oidc.client_secret",
            "OIDC client secret",
            ("set" if settings.oidc_client_secret else "missing") if configured else NOT_CONFIGURED,
            src("oidc_client_secret"),
        ),
        row("oidc.scopes", "OIDC scopes", settings.oidc_scopes, src("oidc_scopes")),
        row(
            "oidc.redirect_uri",
            "Redirect URI to register at the identity provider",
            f"{settings.api_url.rstrip('/')}/api/v1/auth/callback",
            "derived",
        ),
        row(
            "oidc.discovery",
            "Discovery document",
            f"{issuer}/.well-known/openid-configuration" if configured else NOT_CONFIGURED,
            "derived",
        ),
        row(
            "oidc.default_org",
            "Organisation for unprefixed groups",
            settings.oidc_default_org or "none",
            src("oidc_default_org"),
        ),
        row(
            "oidc.per_organisation",
            "Organisations' own identity providers",
            "declared by an extension" if has_resolver() else "instance provider only",
            "derived",
        ),
        row(
            "mcp.oauth.enabled",
            "MCP door accepts OAuth tokens",
            "enabled" if settings.mcp_oauth_enabled else "disabled",
            src("mcp_oauth_enabled"),
        ),
    ]
    if settings.mcp_oauth_enabled:
        rows.append(row("mcp.oauth.issuer", "MCP OAuth issuer", emetteur(settings), "derived"))
        rows.append(
            row(
                "mcp.oauth.audience",
                "MCP OAuth audience",
                settings.mcp_oauth_audience,
                src("mcp_oauth_audience"),
            )
        )
        for name, client in sorted(settings.mcp_oauth_clients.items()):
            port = f", callback port {client.callback_port}" if client.callback_port else ""
            rows.append(
                row(
                    f"mcp.oauth.client.{name}",
                    f"MCP OAuth client {name}",
                    f"{client.client_id}{port}",
                    "settings",
                )
            )
    rows += [
        row(
            "session.max_age",
            "Session lifetime",
            f"{settings.session_max_age_s // 3600} h"
            if settings.session_max_age_s % 3600 == 0
            else f"{settings.session_max_age_s} s",
            src("session_max_age_s"),
        ),
        row(
            "dev_login.enabled",
            "Development sign-in",
            "enabled" if settings.dev_login_enabled else "disabled",
            src("dev_login_enabled"),
        ),
        row(
            "bootstrap.org", "Bootstrap organisation", settings.bootstrap_org or "none", src("bootstrap_org")
        ),
        row(
            "bootstrap.admins",
            "Bootstrap administrators",
            str(len(settings.bootstrap_admin_emails)),
            src("bootstrap_admins"),
        ),
    ]
    return rows


@router.get("/platform/identity", response_model=PlatformIdentity, operation_id="getPlatformIdentity")
async def get_platform_identity(session: Db, principal: Me, settings: Config) -> PlatformIdentity:
    await exiger_admin_de_plateforme(session, principal)
    return PlatformIdentity(items=_rows(settings))
