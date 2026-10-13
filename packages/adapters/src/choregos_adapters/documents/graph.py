# SPDX-License-Identifier: Apache-2.0
"""SharePoint and OneDrive through Microsoft Graph, with delta sync (S33-04).

The Entra application holds **`Sites.Selected`** (application permission) and is granted `read` on
the chosen site(s) only — never `Files.Read.All` or `Sites.Read.All`, which would open every site of
the tenant. A OneDrive for Business is a site too: grant it the same way, then name its drive.

`/drives/{id}/root/delta` lists what changed since the last `deltaLink`; the first call lists
everything. Items carrying a `deleted` facet were deleted at the source. The download follows
Graph's redirect to a pre-authenticated URL; httpx does not forward the bearer to another host.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import UpstreamError
from .base import Changes, SourceItem

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
#: Delta pages followed in one sync, at most: a drive larger than that resumes at the next sync.
MAX_PAGES = 200


class GraphDriveSource:
    def __init__(  # noqa: PLR0913 - keyword-only: the application, then the drive
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        site_id: str | None = None,
        drive_id: str | None = None,
        folder: str | None = None,
        graph_url: str = GRAPH,
        login_url: str = LOGIN,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not site_id and not drive_id:
            raise ValueError("a SharePoint or OneDrive source names a `site_id` or a `drive_id`")
        self.tenant_id = tenant_id
        self._client_id = client_id
        self._secret = client_secret
        self.site_id = site_id
        self.drive_id = drive_id
        self.folder = "/" + folder.strip("/") if folder else None
        self.graph_url = graph_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=60.0, transport=transport, follow_redirects=True)
        self._token: tuple[str, float] | None = None

    async def _headers(self) -> dict[str, str]:
        if self._token is None or self._token[1] < time.monotonic() + 60:
            response = await self._http.post(
                f"{self.login_url}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            if response.status_code >= 400:
                raise UpstreamError(
                    "graph", f"token refused ({response.status_code})", status_code=response.status_code
                )
            body = response.json()
            self._token = (str(body["access_token"]), time.monotonic() + float(body.get("expires_in", 3600)))
        return {"Authorization": f"Bearer {self._token[0]}"}

    async def _get(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url, headers=await self._headers())
        if response.status_code >= 400:
            raise UpstreamError("graph", f"GET {url} → {response.status_code}: {response.text[:300]}",
                                status_code=response.status_code)  # fmt: skip
        return dict(response.json())

    async def _drive(self) -> str:
        if not self.drive_id:
            self.drive_id = str((await self._get(f"{self.graph_url}/sites/{self.site_id}/drive"))["id"])
        return self.drive_id

    def _in_folder(self, item: dict[str, Any]) -> bool:
        if not self.folder:
            return True
        path = str((item.get("parentReference") or {}).get("path", ""))
        # `/drive/root:/Policies/Security` → `/Policies/Security`
        inside = path.split("root:", 1)[1] if "root:" in path else path
        return inside == self.folder or inside.startswith(self.folder + "/")

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        drive = await self._drive()
        url = (cursor or {}).get("delta_link") or f"{self.graph_url}/drives/{drive}/root/delta"
        changes = Changes()
        for _ in range(MAX_PAGES):
            page = await self._get(str(url))
            for item in page.get("value", []):
                ref = str(item["id"])
                if "deleted" in item:
                    changes.deleted.append(ref)
                elif "file" in item and self._in_folder(item):
                    changes.items.append(
                        SourceItem(
                            ref=ref,
                            name=str(item.get("name", ref)),
                            version=str(
                                item.get("cTag") or item.get("eTag") or item.get("lastModifiedDateTime", "")
                            ),
                            url=item.get("webUrl"),
                            size=item.get("size"),
                        )
                    )
                elif "file" in item:
                    # Moved out of the folder: it leaves the knowledge base.
                    changes.deleted.append(ref)
            if "@odata.deltaLink" in page:
                changes.cursor = {"delta_link": page["@odata.deltaLink"], "drive_id": drive}
                return changes
            url = page.get("@odata.nextLink")
            if not url:
                break
        # Not finished in one sync: keep where we are; the next sync goes on from there.
        changes.cursor = {"delta_link": url, "drive_id": drive} if url else cursor
        return changes

    async def fetch(self, item: SourceItem) -> bytes:
        drive = await self._drive()
        response = await self._http.get(
            f"{self.graph_url}/drives/{drive}/items/{item.ref}/content", headers=await self._headers()
        )
        if response.status_code >= 400:
            raise UpstreamError("graph", f"download of {item.name} → {response.status_code}",
                                status_code=response.status_code)  # fmt: skip
        return response.content


class GraphDocumentsConnector:
    """The organisation's `documents/sharepoint` connector: one Entra application (`Sites.Selected`),
    from which each knowledge source opens the drive it names."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def source(
        self, *, site_id: str | None, drive_id: str | None, folder: str | None, key: str
    ) -> GraphDriveSource:
        return GraphDriveSource(
            tenant_id=self.config["tenant_id"],
            client_id=self.config["client_id"],
            client_secret=self.config.get("client_secret", ""),
            site_id=site_id,
            drive_id=drive_id,
            folder=folder,
            graph_url=self.config.get("graph_url", GRAPH),
            login_url=self.config.get("login_url", LOGIN),
        )
