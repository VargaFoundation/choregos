# SPDX-License-Identifier: Apache-2.0
"""An A2A Agent Card: fetched, parsed, digested (ADR 0062).

A card says where an agent is (`url`), which protocol version it speaks, what it can do (`skills`)
and how a caller proves who it is (`securitySchemes`). The platform PINS a card when a remote agent
is published: the digest of what it read. The runner reads the card again before it sends anything
and refuses a card that changed — a remote agent whose endpoint, skills or security moved under the
registry's feet is not the agent somebody reviewed.

The parser is tolerant of both A2A 0.3 (`protocolVersion`, `securitySchemes`, `capabilities`) and
the 1.0 shape (`supportedInterfaces`); it never invents a field the card does not carry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

#: A card is a small JSON document; anything bigger is not a card.
MAX_CARD_BYTES = 512 * 1024
#: The credential types an organisation can hold for a remote agent (ADR 0062).
AUTH_OAUTH = "oauth2_authorization_code"
AUTH_BEARER = "bearer"
AUTH_NONE = "none"
DCR_EXTENSIONS = ("setup-dcr", "dynamic-client-registration", "dcr")


class CardError(RuntimeError):
    """The card could not be fetched or is not a usable Agent Card."""


@dataclass(frozen=True)
class CardSkill:
    id: str
    name: str = ""
    description: str = ""


@dataclass(frozen=True)
class AgentCard:
    raw: dict[str, Any]
    url: str
    name: str
    description: str
    protocol_version: str
    skills: tuple[CardSkill, ...]
    streaming: bool
    #: `oauth2_authorization_code` | `bearer` | `none`: the schemes the card accepts, in its order.
    auth_types: tuple[str, ...]
    token_url: str | None = None
    authorization_url: str | None = None
    scopes: tuple[str, ...] = ()
    #: The Dynamic Client Registration endpoint a card advertises in an extension, if any.
    dcr_url: str | None = None
    digest: str = field(default="")

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def skill(self, skill_id: str) -> CardSkill | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    @property
    def major(self) -> int:
        head = self.protocol_version.split(".", 1)[0]
        return int(head) if head.isdigit() else 0


def digest_of(card: dict[str, Any]) -> str:
    """`sha256:<hex>` of the card's canonical JSON: any change, even of one character, moves it."""
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_card(raw: dict[str, Any]) -> AgentCard:
    if not isinstance(raw, dict):
        raise CardError("an Agent Card is a JSON object")
    url = raw.get("url")
    if not url:
        interfaces = raw.get("supportedInterfaces") or raw.get("additionalInterfaces") or []
        url = next((i.get("url") for i in interfaces if isinstance(i, dict) and i.get("url")), None)
    if not isinstance(url, str) or not url:
        raise CardError("the card names no endpoint (`url`)")
    version = str(raw.get("protocolVersion") or "")
    if not version:
        interfaces = raw.get("supportedInterfaces") or []
        version = next(
            (
                str(i["protocolVersion"])
                for i in interfaces
                if isinstance(i, dict) and i.get("protocolVersion")
            ),
            "0.3.0",
        )
    skills = tuple(
        CardSkill(str(s["id"]), str(s.get("name") or s["id"]), str(s.get("description") or ""))
        for s in raw.get("skills") or []
        if isinstance(s, dict) and s.get("id")
    )
    capabilities = raw.get("capabilities") or {}
    auth, token_url, auth_url, scopes = _security(raw)
    return AgentCard(
        raw=raw,
        url=url,
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        protocol_version=version,
        skills=skills,
        streaming=bool(capabilities.get("streaming")),
        auth_types=auth,
        token_url=token_url,
        authorization_url=auth_url,
        scopes=scopes,
        dcr_url=_dcr_url(capabilities),
        digest=digest_of(raw),
    )


def _security(raw: dict[str, Any]) -> tuple[tuple[str, ...], str | None, str | None, tuple[str, ...]]:
    schemes = raw.get("securitySchemes") or {}
    kinds: list[str] = []
    token_url: str | None = None
    auth_url: str | None = None
    scopes: tuple[str, ...] = ()
    for scheme in schemes.values() if isinstance(schemes, dict) else []:
        if not isinstance(scheme, dict):
            continue
        # 0.3 spells `type`; 1.0 wraps the scheme under its kind.
        kind = str(scheme.get("type") or next(iter(scheme), "")).lower()
        if kind in {"oauth2", "oauth2securityscheme"}:
            flow = ((scheme.get("flows") or scheme.get("oauth2SecurityScheme", {}).get("flows")) or {}).get(
                "authorizationCode"
            )
            if isinstance(flow, dict):
                kinds.append(AUTH_OAUTH)
                token_url = flow.get("tokenUrl")
                auth_url = flow.get("authorizationUrl")
                scopes = tuple((flow.get("scopes") or {}).keys())
        elif kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
            kinds.append(AUTH_BEARER)
        elif kind in {"apikey", "openidconnect", "mutualtls"}:
            continue
    if not schemes and not raw.get("security"):
        kinds.append(AUTH_NONE)
    return tuple(dict.fromkeys(kinds)), token_url, auth_url, scopes


def _dcr_url(capabilities: dict[str, Any]) -> str | None:
    for extension in capabilities.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        uri = str(extension.get("uri") or "").lower()
        target = (extension.get("params") or {}).get("target_url")
        if target and any(marker in uri for marker in DCR_EXTENSIONS):
            return str(target)
    return None


async def fetch_card(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> AgentCard:
    """Fetches and parses a card. https only, no redirect to another host, bounded in size and time.

    A card URL is typed by an administrator but fetched by the platform: this is the SSRF guard of
    that fetch. A plain-http card is refused (a card carries the endpoint a token will be sent to),
    except for the loopback tests use.
    """
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise CardError(f"the card URL must be https: {url}")
    async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        redirects = 0
        while response.is_redirect and redirects < 3:
            target = urlsplit(str(response.next_request.url)) if response.next_request else None
            if target is None or target.hostname != parts.hostname:
                raise CardError("the card URL redirects to another host: refused")
            response = await client.send(response.next_request) if response.next_request else response
            redirects += 1
    if response.status_code != 200:
        raise CardError(f"the card answered HTTP {response.status_code}")
    if len(response.content) > MAX_CARD_BYTES:
        raise CardError("the card is too large")
    try:
        raw = response.json()
    except ValueError as exc:
        raise CardError("the card is not JSON") from exc
    return parse_card(raw)
