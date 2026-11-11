# SPDX-License-Identifier: Apache-2.0
"""The `choregos` CLI, served by the instance it talks to (S62-08).

A person installs the CLI from their own Choregos, in the exact version it runs, through the host
of the console: no firewall to open towards GitHub, and it works on a network without internet.
The native binaries (S62-07) lie in `CHOREGOS_CLI_DIR` (`/opt/choregos/cli` in the API image) with
`SHA256SUMS` and `manifest.json` (the shared contract of S62-07/08):

- `GET /downloads/cli/manifest.json`: the manifest, as the build wrote it, once checked.
- `GET /downloads/cli/<name>`: a file the manifest lists, or `SHA256SUMS`. Nothing else: the name
  is looked up in the manifest, never joined to a path, so `../` reaches nothing.
- `GET /cli/install.sh`, `GET /cli/install.ps1`: the install scripts, with this instance's public
  URL written in them; they fetch the manifest and the binary from the same host and verify the
  sha256 before installing.

Public, like `/healthz` and `/edition`, on purpose: the CLI is open source and carries no secret,
the scripts must work with a plain `curl | sh` or `irm | iex` before the person has any token, and
the manifest says only the version `/edition` already says. The bandwidth is what needs guarding:
the routes have a rate limit of their own (`CHOREGOS_DOWNLOAD_RATE_LIMIT_PER_MINUTE`).

Outside the OpenAPI contract, like `/metrics` and the MCP door: these are files and scripts, not
API resources, and a contract change is a `contract-change` PR of its own (ADR 0001).
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .. import __version__
from ..config import get_settings
from ..errors import ApiError

router = APIRouter(tags=["session"])

#: The release page of a version, where the binaries are attached under the same names.
RELEASES = "https://github.com/VargaFoundation/choregos/releases"
MANIFEST = "manifest.json"
SUMS = "SHA256SUMS"
#: A file name the manifest may list: one path segment, no leading dot.
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
#: What may be written into a script as the instance's URL, between single quotes (sh) or double
#: quotes (PowerShell): no quote, no `$`, no backtick, no space, no backslash.
_PLAIN_URL = re.compile(r"https?://[A-Za-z0-9.\-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~\-/%]*)?")
#: The token each script carries where the instance's URL goes.
PLACEHOLDER = "__CHOREGOS_INSTANCE_URL__"
#: The longest manifest read: five entries weigh well under a kilobyte.
_MANIFEST_MAX_BYTES = 64 * 1024


class CliFile(BaseModel):
    name: Annotated[str, StringConstraints(pattern=_NAME)]
    os: Literal["linux", "macos", "windows"]
    arch: Literal["x86_64", "arm64"]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    size: int = Field(ge=0)


class CliManifest(BaseModel):
    version: Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z][0-9A-Za-z.+\-]{0,63}$")]
    files: list[CliFile]


def release_url(version: str = __version__) -> str:
    return f"{RELEASES}/tag/v{version}"


def _no_cli_bundled() -> ApiError:
    return ApiError(
        status.HTTP_404_NOT_FOUND,
        "No CLI bundled",
        f"this build of Choregos bundles no `choregos` CLI; download version {__version__} from "
        f"{release_url()}",
        type_="https://choregos.dev/problems/no-cli-bundled",
        extra={"code": "no_cli_bundled", "version": __version__, "release_url": release_url()},
    )


def _broken(detail: str) -> ApiError:
    """The bundle is there but cannot be trusted: say so rather than serve it."""
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "CLI bundle unusable",
        f"{detail}; download version {__version__} from {release_url()}",
        type_="https://choregos.dev/problems/cli-bundle-unusable",
        extra={"code": "cli_bundle_unusable", "version": __version__, "release_url": release_url()},
    )


def cli_dir() -> Path:
    return Path(get_settings().cli_dir)


def read_manifest(directory: Path) -> tuple[CliManifest, bytes]:
    """The checked manifest and its raw bytes; 404 without a bundle, 503 for a manifest that does not hold."""
    path = directory / MANIFEST
    if not path.is_file():
        raise _no_cli_bundled()
    raw = path.read_bytes()
    if len(raw) > _MANIFEST_MAX_BYTES:
        raise _broken(f"the bundled {MANIFEST} is larger than {_MANIFEST_MAX_BYTES} bytes")
    try:
        manifest = CliManifest.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise _broken(f"the bundled {MANIFEST} is invalid ({type(exc).__name__})") from None
    names = [entry.name for entry in manifest.files]
    if len(set(names)) != len(names) or {MANIFEST, SUMS} & set(names):
        raise _broken(f"the bundled {MANIFEST} lists a name twice, or names itself")
    return manifest, raw


def _etag(sha256: str) -> str:
    return f'"sha256-{sha256}"'


def _not_modified(request: Request, etag: str) -> bool:
    asked = request.headers.get("if-none-match", "")
    return any(candidate.strip() in {etag, "*"} for candidate in asked.split(","))


@router.get("/downloads/cli/manifest.json", include_in_schema=False)
async def cli_manifest(request: Request) -> Response:
    """The manifest of the bundled CLI, as the build wrote it (its own bytes, its own checksums)."""
    _, raw = read_manifest(cli_dir())
    etag = _etag(hashlib.sha256(raw).hexdigest())
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if _not_modified(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=raw, media_type="application/json", headers=headers)


@router.get("/downloads/cli/{name}", include_in_schema=False)
async def cli_file(name: str, request: Request) -> Response:
    """One file of the bundle: a binary the manifest lists, or `SHA256SUMS`.

    The ETag is the manifest's sha256 of the file (strong: the bytes, not a date), so a client that
    holds the file revalidates it for nothing; `Range` is honoured, so a broken download resumes.
    """
    directory = cli_dir()
    manifest, _ = read_manifest(directory)
    if name == SUMS:
        sums = directory / SUMS
        if not sums.is_file():
            raise ApiError(status.HTTP_404_NOT_FOUND, "Not found", f"this bundle has no {SUMS}")
        return FileResponse(
            sums, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"}
        )
    entry = next((candidate for candidate in manifest.files if candidate.name == name), None)
    if entry is None:
        listed = ", ".join(candidate.name for candidate in manifest.files) or "none"
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "Not found",
            f"`{name}` is not a file of the bundled CLI (it holds: {listed})",
        )
    path = directory / entry.name
    if not path.is_file():
        raise _broken(f"`{entry.name}` is in the manifest but not in the bundle")
    if path.stat().st_size != entry.size:
        raise _broken(f"`{entry.name}` does not have the size its manifest gives")
    etag = _etag(entry.sha256)
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Checksum-Sha256": entry.sha256}
    if _not_modified(request, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=entry.name,
        headers=headers,
    )


def instance_url() -> str:
    """The URL a person reaches this instance at: the console's, which routes `/downloads` here."""
    url = get_settings().public_url.strip().rstrip("/")
    if not _PLAIN_URL.fullmatch(url):
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Install script unavailable",
            "CHOREGOS_PUBLIC_URL is not a plain http(s) URL, so it cannot be written into an install script",
        )
    return url


def render_script(name: Literal["install.sh", "install.ps1"], url: str) -> str:
    """An install script with `url` written in it. `url` must already be a plain URL (`instance_url`)."""
    if not _PLAIN_URL.fullmatch(url):
        raise ValueError(f"not a plain http(s) URL: {url!r}")
    template = resources.files("choregos_api").joinpath("cli_install", name).read_text(encoding="utf-8")
    return template.replace(PLACEHOLDER, url)


def _script(name: Literal["install.sh", "install.ps1"]) -> Response:
    # Plain text: a browser shows it rather than runs or downloads it, and `irm` returns a string.
    return Response(
        content=render_script(name, instance_url()),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/cli/install.sh", include_in_schema=False)
async def install_sh() -> Response:
    """`curl -fsSL <instance>/cli/install.sh | sh`: Linux and macOS, into `~/.local/bin`."""
    return _script("install.sh")


@router.get("/cli/install.ps1", include_in_schema=False)
async def install_ps1() -> Response:
    """`irm <instance>/cli/install.ps1 | iex`: Windows, into `%LOCALAPPDATA%\\Programs\\choregos`."""
    return _script("install.ps1")
