# SPDX-License-Identifier: Apache-2.0
"""What the Azure DevOps connectors of source control and CI share (ADR 0209, S13-08).

Azure DevOps Services (`https://dev.azure.com/<organisation>`) and Azure DevOps Server 2022 (the
collection, `https://<server>/<virtual directory>/<collection>`) speak the same REST API 7.1: the
connector's `base_url` is the only difference. The credential is a **personal access token** (HTTP
Basic with an empty user name, the only one a Server accepts) or, on the Services only, a **Microsoft
Entra service principal** through the client credentials grant, fetched here for the Azure DevOps
resource and refreshed a minute before it expires.

This is the one Azure DevOps client of the three connector types (Boards, Repos, Pipelines): the REST
client, the Entra client credentials, the PAT handling, the configuration checks, the egress hosts and the
service hook's shared secret live here and nowhere else (ADR 0209; `tests/test_azure_one_client.py`). The
Boards tracker imports this module; nothing here imports the tracker.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

from .errors import ConfigurationError, UpstreamError
from .http import RestClient

API_VERSION = "7.1"
#: The application id of Azure DevOps in Microsoft Entra: the `.default` scope of a token for it.
AZURE_DEVOPS_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
LOGIN_URL = "https://login.microsoftonline.com"
JSON_PATCH = {"Content-Type": "application/json-patch+json"}
SHA = re.compile(r"^[0-9a-f]{40}$")
ZERO_SHA = "0" * 40


class EntraClientCredentials:
    """A Microsoft Entra service principal's access token for Azure DevOps (RFC 6749 §4.4), cached.

    The token is requested through the REST client's own transport, so a test scripts the token
    endpoint like any other call. Only the Azure DevOps Services accept it."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        login_url: str = LOGIN_URL,
        service: str = "azure-devops",
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._secret = client_secret
        self.login_url = login_url.rstrip("/")
        self.service = service
        self._token: tuple[str, float] | None = None

    async def token(self, client: Any) -> str:
        if self._token is None or self._token[1] < time.monotonic() + 60:
            response = await client.post(
                f"{self.login_url}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._secret,
                    "scope": f"{AZURE_DEVOPS_RESOURCE}/.default",
                },
            )
            if response.status_code >= 400:
                raise UpstreamError(
                    self.service,
                    f"the Entra token endpoint refused the service principal ({response.status_code})",
                    status_code=response.status_code,
                )
            body = response.json()
            self._token = (str(body["access_token"]), time.monotonic() + float(body.get("expires_in", 3600)))
        return self._token[0]


class AzureDevOpsClient(RestClient):
    """The REST client of Azure DevOps: `api-version` on every call, a PAT or an Entra token as credential."""

    def __init__(
        self,
        base_url: str,
        *,
        pat: str | None = None,
        entra: EntraClientCredentials | None = None,
        **kwargs: Any,
    ) -> None:
        if bool(pat) == bool(entra):
            raise ConfigurationError(
                "an Azure DevOps connector needs a personal access token or an Entra service principal"
            )
        super().__init__(base_url, auth=("", pat) if pat else None, **kwargs)
        self.entra = entra

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        params = dict(kwargs.pop("params", None) or {})
        params.setdefault("api-version", API_VERSION)
        kwargs["params"] = params
        if self.entra is not None:
            headers = dict(kwargs.pop("headers", None) or {})
            headers["Authorization"] = f"Bearer {await self.entra.token(self._client)}"
            kwargs["headers"] = headers
        try:
            return await super().request(method, path, **kwargs)
        except json.JSONDecodeError as page:
            # A refused PAT is not a 401 on some servers: it is a 203 and the HTML of the sign-in page.
            raise UpstreamError(
                self.service,
                f"203 on {method} {path}: Azure DevOps answered a page, not JSON "
                "(a sign-in page means the token is wrong, expired or revoked)",
                status_code=203,
            ) from page


def azure_client(cfg: Mapping[str, Any], *, service: str) -> AzureDevOpsClient:
    """The REST client of one Azure DevOps connector's configuration: a PAT or an Entra principal."""
    entra = (
        EntraClientCredentials(
            str(cfg["tenant_id"]),
            str(cfg["client_id"]),
            str(cfg.get("client_secret") or ""),
            login_url=str(cfg.get("login_url") or LOGIN_URL),
            service=service,
        )
        if cfg.get("auth") == "entra"
        else None
    )
    return AzureDevOpsClient(
        str(cfg["base_url"]), pat=None if entra else str(cfg.get("pat") or ""), entra=entra, service=service
    )


def check_azure_devops(cfg: Mapping[str, Any], *, needs_repository: bool = False) -> None:
    """The collection to call, the team project, and the credential that fits: a personal access token
    anywhere, an Entra service principal on the Azure DevOps Services only (a Server 2022 has no Entra
    sign-in for an application)."""
    from .registry import fakes_enabled

    url = str(cfg.get("base_url") or "").strip()
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a port out of range
    except ValueError as invalid:
        raise ConfigurationError("`base_url` is not a valid address") from invalid
    if parts.scheme not in ({"https", "http"} if fakes_enabled() else {"https"}) or not parts.hostname:
        raise ConfigurationError(
            "`base_url` must be the https address of the organisation or the collection "
            "(https://dev.azure.com/<organisation>)"
        )
    if not str(cfg.get("project") or "").strip():
        raise ConfigurationError("`project` is required: the Azure DevOps team project")
    if needs_repository and not str(cfg.get("repository") or "").strip():
        raise ConfigurationError("`repository` is required: the Azure Repos Git repository (name or id)")
    auth = cfg.get("auth", "pat")
    if auth not in {"pat", "entra"}:
        raise ConfigurationError("`auth` is `pat` (a personal access token) or `entra` (a service principal)")
    if auth == "entra":
        for field in ("tenant_id", "client_id"):
            if not str(cfg.get(field) or "").strip():
                raise ConfigurationError(f"`auth: entra` needs `{field}`")
        host = (parts.hostname or "").lower()
        if host != "dev.azure.com" and not host.endswith(".visualstudio.com") and not fakes_enabled():
            raise ConfigurationError(
                "`auth: entra` works on Azure DevOps Services (dev.azure.com) only: "
                "Azure DevOps Server takes a personal access token"
            )


def check_azure_repos(cfg: Mapping[str, Any]) -> None:
    check_azure_devops(cfg, needs_repository=True)
    if cfg.get("merge_strategy", "squash") not in MERGE_STRATEGIES:
        raise ConfigurationError(f"`merge_strategy` is one of {', '.join(MERGE_STRATEGIES)}")
    _check_partial(cfg)


def check_azure_boards(cfg: Mapping[str, Any]) -> None:
    check_azure_devops(cfg)


def check_azure_pipelines(cfg: Mapping[str, Any]) -> None:
    check_azure_devops(cfg)
    _check_partial(cfg)


def _check_partial(cfg: Mapping[str, Any]) -> None:
    if cfg.get("partially_succeeded", "failure") not in {"failure", "neutral"}:
        raise ConfigurationError("`partially_succeeded` is `failure` (the default) or `neutral`")


#: `completionOptions.mergeStrategy` of a completed pull request.
MERGE_STRATEGIES = ("squash", "noFastForward", "rebase", "rebaseMerge")


def azure_egress(config: Mapping[str, Any], *, identities: bool = True) -> list[tuple[str, int]]:
    """The organisation's (or the Server's) host; the identity host (`vssps.dev.azure.com`, where Repos
    resolves reviewers; `identities=False` for Boards, which never calls it) and the Entra login host
    for a service principal on the Services."""
    from tranibor_core.egress import hosts_of_config

    hosts = hosts_of_config(dict(config))
    host = (urlsplit(str(config.get("base_url") or "")).hostname or "").lower()
    extra: list[str] = []
    if identities and host == "dev.azure.com":
        extra.append("vssps.dev.azure.com")
    if config.get("auth") == "entra":
        extra.append(
            urlsplit(str(config.get("login_url") or LOGIN_URL)).hostname or "login.microsoftonline.com"
        )
    for name in extra:
        if (name, 443) not in hosts:
            hosts.append((name, 443))
    return hosts


def azure_boards_egress(config: Mapping[str, Any]) -> list[tuple[str, int]]:
    return azure_egress(config, identities=False)


def shared_secret_of(headers: Mapping[str, str]) -> str:
    """The secret a service hook delivery carries: `X-Choregos-Secret` (a custom HTTP header of the
    subscription), else the password of its `Authorization: Basic` header (any user name). Empty when none."""
    lowered = {name.lower(): value for name, value in headers.items()}
    header = lowered.get("x-choregos-secret", "")
    if header:
        return header
    scheme, _, token = lowered.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not token:
        return ""
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return ""
    return decoded.partition(":")[2]


def segment(value: str) -> str:
    return quote(value, safe="")
