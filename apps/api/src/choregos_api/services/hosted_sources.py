# SPDX-License-Identifier: Apache-2.0
"""A hosted agent's source zip (ADR 0080 §9): checked once, stored by its digest, built from a
Dockerfile the platform writes.

What a zip must hold, checked at upload and again when a version names it:

- a flat archive: the agent's files at its root, not under one folder. A zip of the folder rather
  than of its content is refused with the reason, never silently unwrapped: the entry point names a
  path, and it must mean the same thing in the zip and in the image;
- no symbolic link, no absolute, `\\` or `..` path, no encrypted entry, no name twice; at most
  2,000 files and 64 MiB, uncompressed bytes counted as they are read (a zip bomb stops there);
- for a version: a runtime the platform has a base image for, the entry point's script inside the
  zip, and with `remote_build` a `requirements.txt` or a `pyproject.toml` at the root.

The Dockerfile is a pure function of the version and the base image: nothing of the zip's own
(a `Dockerfile` it carries is not read). The build key is the hash of that Dockerfile and of the zip,
so the same inputs never build twice (S41-08).
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from choregos_adapters.storage import ObjectNotFoundError, ObjectStore, ObjectStoreError

from ..config import get_settings
from ..errors import unprocessable, upstream

if TYPE_CHECKING:
    from ..schemas import AgentSpec, HostedBuild, HostedRuntime, HostedZipSource

MAX_ENTRIES = 2000
MAX_BYTES = 64 * 1024 * 1024
#: The Dockerfile's shape: a new version changes every build key, so every zip builds again.
TEMPLATE_VERSION = "1"
#: The base image of each runtime the platform builds, pinned by digest; the operator replaces them
#: with `CHOREGOS_HOSTED_BASE_IMAGES`. Node, .NET and Python 3.14 come with S41-20.
DEFAULT_BASE_IMAGES = {
    "python_3_12": "docker.io/library/python:3.12-slim-bookworm"
    "@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258",
    "python_3_13": "docker.io/library/python:3.13-slim-bookworm"
    "@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641",
}
PYTHON_DEPENDENCIES = ("requirements.txt", "pyproject.toml")
#: Where a `bundled` Python zip keeps its dependencies: `pip install --target site-packages`.
BUNDLED_PACKAGES = "site-packages"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class SourceRefused(ValueError):  # noqa: N818 - a refusal with its reason, answered as 422
    pass


@dataclass(frozen=True)
class SourceArchive:
    """What a checked zip holds: its digest, its size, and its files' paths."""

    sha256: str
    size_bytes: int
    files: tuple[str, ...]

    def has(self, path: str) -> bool:
        return path in self.files


def storage_key(org_id: str, sha256: str) -> str:
    return f"hosted-sources/{org_id}/{sha256.removeprefix('sha256:')}.zip"


def _safe_path(name: str) -> str:
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise SourceRefused(f"`{name}`: a relative path with `/` separators is expected")
    normal = posixpath.normpath(name)
    if normal == ".." or normal.startswith("../") or "/../" in f"/{normal}/":
        raise SourceRefused(f"`{name}` goes outside the archive")
    return normal


def read_source(data: bytes) -> SourceArchive:
    """The checked content of a source zip; `SourceRefused` with the first reason otherwise."""
    if len(data) > MAX_BYTES:
        raise SourceRefused(f"the archive is larger than {MAX_BYTES // (1024 * 1024)} MiB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceRefused("this is not a zip archive") from exc
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if not entries:
            raise SourceRefused("the archive holds no file")
        if len(entries) > MAX_ENTRIES:
            raise SourceRefused(f"{len(entries)} files: {MAX_ENTRIES} at most")
        files: set[str] = set()
        read = 0
        for info in entries:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise SourceRefused(f"`{info.filename}` is a symbolic link: refused")
            if info.flag_bits & 0x1:
                raise SourceRefused(f"`{info.filename}` is encrypted: refused")
            path = _safe_path(info.filename)
            if path in files:
                raise SourceRefused(f"`{path}` is in the archive twice")
            files.add(path)
            with archive.open(info) as entry:
                while chunk := entry.read(1024 * 1024):
                    read += len(chunk)
                    if read > MAX_BYTES:
                        raise SourceRefused(f"more than {MAX_BYTES // (1024 * 1024)} MiB once uncompressed")
    roots = {path.split("/", 1)[0] for path in files}
    if len(roots) == 1 and all("/" in path for path in files):
        (root,) = roots
        raise SourceRefused(
            f"every file is under `{root}/`: zip the folder's content, not the folder "
            "(the entry point is a path from the archive's root)"
        )
    return SourceArchive(
        sha256="sha256:" + hashlib.sha256(data).hexdigest(), size_bytes=len(data), files=tuple(sorted(files))
    )


def dependency_file(archive: SourceArchive) -> str | None:
    return next((name for name in PYTHON_DEPENDENCIES if archive.has(name)), None)


def base_image(runtime: str) -> str:
    """The pinned base image of a runtime; `SourceRefused` when the platform has none."""
    images = {**DEFAULT_BASE_IMAGES, **get_settings().hosted_base_images}
    image = images.get(runtime)
    if not image:
        known = ", ".join(sorted(images)) or "none"
        raise SourceRefused(f"no base image for the runtime `{runtime}` (the platform builds: {known})")
    if "@sha256:" not in image:
        raise SourceRefused(f"the base image of `{runtime}` is not pinned by digest: `{image}`")
    return image


def _script(entry_point: list[str]) -> str | None:
    """The file a Python entry point runs, when it names one: `python [-flags] main.py ...` or
    `./start.sh`. `python -m module` and a bare command (`uvicorn`) come from the dependencies."""
    if entry_point[0] in ("python", "python3"):
        for argument in entry_point[1:]:
            if argument in ("-m", "-c"):
                return None
            if not argument.startswith("-"):
                return argument
        return None
    return entry_point[0] if "/" in entry_point[0] or "." in entry_point[0] else None


def check_against(archive: SourceArchive, source: HostedZipSource) -> None:
    """What a version asks of the zip it names."""
    base_image(source.runtime)
    script = _script(source.entry_point)
    if script is not None and not archive.has(_safe_path(script)):
        raise SourceRefused(f"the entry point runs `{script}`, which is not in the archive")
    if source.dependency_resolution == "remote_build" and dependency_file(archive) is None:
        raise SourceRefused(
            "`remote_build` installs the declared dependencies: the archive has neither "
            "`requirements.txt` nor `pyproject.toml` at its root (or say `bundled`)"
        )


def dockerfile(runtime: HostedRuntime, archive: SourceArchive) -> str:
    """The Dockerfile that builds a zip version; its context is the zip, unpacked."""
    source = runtime.source
    assert source.type == "zip"
    lines = [
        "# syntax=docker/dockerfile:1",
        f"# Written by Choregos (ADR 0080 §9, template {TEMPLATE_VERSION}). The zip's own Dockerfile,",
        "# if it has one, is not read.",
        f"FROM {base_image(source.runtime)}",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 "
        f"PIP_DISABLE_PIP_VERSION_CHECK=1 PORT={runtime.port}",
        "RUN useradd --uid 1000 --create-home agent",
        "WORKDIR /app",
    ]
    declared = dependency_file(archive)
    if source.dependency_resolution == "bundled":
        lines += [f"ENV PYTHONPATH=/app/{BUNDLED_PACKAGES}", "COPY . /app"]
    elif declared == "requirements.txt":
        lines += [
            "COPY requirements.txt /app/requirements.txt",
            "RUN pip install --no-cache-dir -r /app/requirements.txt",
            "COPY . /app",
        ]
    else:
        lines += ["COPY . /app", "RUN pip install --no-cache-dir /app"]
    lines += [
        "USER 1000",
        f"EXPOSE {runtime.port}",
        f"ENTRYPOINT {json.dumps(source.entry_point)}",
    ]
    return "\n".join(lines) + "\n"


def build_key(text: str, archive: SourceArchive) -> str:
    return "sha256:" + hashlib.sha256(f"{text}\n{archive.sha256}".encode()).hexdigest()


def pending_build(runtime: HostedRuntime, archive: SourceArchive) -> HostedBuild:
    """The build a zip version starts with: keyed by its inputs, with them as its provenance."""
    from ..schemas import HostedBuild

    text = dockerfile(runtime, archive)
    source = runtime.source
    assert source.type == "zip"
    return HostedBuild(
        status="pending",
        key=build_key(text, archive),
        provenance={
            "source": archive.sha256,
            "runtime": source.runtime,
            "dependency_resolution": source.dependency_resolution,
            "base_image": base_image(source.runtime),
            "template": TEMPLATE_VERSION,
            "dockerfile": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        },
    )


async def store_source(store: ObjectStore, org_id: str, data: bytes) -> SourceArchive:
    """Checks a zip and writes it under its digest: the same bytes are one object."""
    archive = read_source(data)
    await store.put(storage_key(org_id, archive.sha256), data, "application/zip")
    return archive


async def load_source(store: ObjectStore, org_id: str, sha256: str) -> SourceArchive:
    """A stored zip, checked again, and refused if its bytes no longer hash to its name."""
    if not _DIGEST.match(sha256):
        raise SourceRefused(f"`{sha256}` is not a sha256 digest")
    try:
        data = await store.get(storage_key(org_id, sha256))
    except ObjectNotFoundError as exc:
        raise SourceRefused(
            f"no source `{sha256}` in this organisation: upload it first "
            "(`POST /orgs/{org}/hosted-sources`)"
        ) from exc
    archive = read_source(data)
    if archive.sha256 != sha256:
        raise SourceRefused(f"the stored source `{sha256}` no longer hashes to its name: refused")
    return archive


async def pin_zip_runtime(store: ObjectStore, org_id: str, spec: AgentSpec) -> AgentSpec:
    """The spec of a zip version with its `build` written by the platform: whatever the caller put
    there is replaced. The zip is read from the store and checked against the version."""
    from ..schemas import HostedRuntime

    runtime = spec.runtime
    if not isinstance(runtime, HostedRuntime) or runtime.source.type != "zip":
        return spec
    loc = ["runtime", "source"]
    try:
        archive = await load_source(store, org_id, runtime.source.sha256)
        check_against(archive, runtime.source)
        build = pending_build(runtime, archive)
    except SourceRefused as exc:
        raise unprocessable("the hosted source cannot be built", [{"loc": loc, "msg": str(exc)}]) from exc
    except ObjectStoreError as exc:
        raise upstream("object-store", f"the hosted source cannot be read: {exc}") from exc
    return spec.model_copy(update={"runtime": runtime.model_copy(update={"build": build})})
