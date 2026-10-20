# SPDX-License-Identifier: Apache-2.0
"""Building a hosted agent's image from its zip (ADR 0080 §9): BuildKit in the cluster, Docker on a
laptop, and the same contract for both.

A build is named by its key (the hash of the Dockerfile the platform wrote and of the zip) and its
image is tagged `build-<key>` in the deployment's build repository. Before building, the builder asks
the registry for that tag: when it is there, the image is the one already built and nothing runs
again. A replayed activity, a second version with the same inputs, a worker restarted mid-build: one
build, one image.

`BuildctlBuilder` sends the context to a `buildkitd` the chart deploys, rootless, over its gRPC
address; the zip never leaves the platform's own processes and the build sandbox holds no
object-store credential, only the build repository's push credential. `LocalDockerBuilder` runs
`docker build` and `docker push` for `docker compose` and `make dev-up`. Both run a command, so a
test gives them a scripted one.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import posixpath
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import AdapterError
from .registry import RegistryError, parse_ref

if TYPE_CHECKING:
    from ..base import RegistryClient

#: What a build's log keeps for the version: the end, where a failing `pip install` says why.
LOG_TAIL = 4000
#: A command's run: argv in, (exit code, output) out.
Runner = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]


class BuildError(AdapterError):
    """The build ran and failed (a `pip install` that cannot resolve, a missing file): building the
    same inputs again fails the same way, so it is not retried."""


@dataclass(frozen=True)
class BuildRequest:
    """One build: its key, the Dockerfile the platform wrote, the zip, and where the image goes."""

    key: str
    dockerfile: str
    context: bytes
    repository: str

    @property
    def tag(self) -> str:
        return "build-" + self.key.removeprefix("sha256:")

    @property
    def reference(self) -> str:
        return f"{self.repository}:{self.tag}"


@dataclass(frozen=True)
class BuildResult:
    #: `repository@sha256:…`
    image: str
    builder: str
    #: Whether this call built it, or found it already built under its key.
    built: bool
    log: str = ""


async def run_command(argv: Sequence[str]) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode(errors="replace")


def unpack(context: bytes, target: Path) -> None:
    """The zip's files under `target`. The API checked the archive already; a path that would land
    outside `target` is refused here again rather than trusted."""
    with zipfile.ZipFile(io.BytesIO(context)) as archive:
        for info in archive.infolist():
            name = posixpath.normpath(info.filename)
            if info.filename.startswith("/") or name == ".." or name.startswith("../"):
                raise BuildError(f"`{info.filename}` goes outside the build context")
            if info.is_dir():
                continue
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(info))


class _CommandBuilder:
    name = "command"

    def __init__(self, registry: RegistryClient | None = None, *, run: Runner | None = None) -> None:
        self.registry = registry
        self.run = run or run_command

    async def already_built(self, request: BuildRequest) -> str | None:
        """The digest under the build's tag when the registry has it. A registry that does not
        answer is not proof of absence, but building again is only slower, never wrong."""
        if self.registry is None:
            return None
        with contextlib.suppress(RegistryError):
            return await self.registry.resolve(request.reference)
        return None

    async def build(self, request: BuildRequest) -> BuildResult:
        parse_ref(request.reference)  # a malformed repository fails before anything runs
        digest = await self.already_built(request)
        if digest is not None:
            return BuildResult(f"{request.repository}@{digest}", self.name, built=False)
        with tempfile.TemporaryDirectory(prefix="choregos-build-") as workdir:
            root = Path(workdir)
            (root / "context").mkdir()
            (root / "dockerfile").mkdir()
            unpack(request.context, root / "context")
            (root / "dockerfile" / "Dockerfile").write_text(request.dockerfile, encoding="utf-8")
            digest, log = await self._build(request, root)
        return BuildResult(f"{request.repository}@{digest}", self.name, built=True, log=log[-LOG_TAIL:])

    async def _build(self, request: BuildRequest, root: Path) -> tuple[str, str]:
        raise NotImplementedError

    async def _check(self, argv: Sequence[str], what: str) -> str:
        code, output = await self.run(argv)
        if code != 0:
            raise BuildError(f"{what} failed ({code}): {output[-LOG_TAIL:]}")
        return output


class BuildctlBuilder(_CommandBuilder):
    """`buildctl` against the deployment's `buildkitd`: the context goes over the session, the image
    is pushed by the daemon, and the digest is read from the metadata file it writes."""

    name = "buildkit"

    def __init__(
        self,
        address: str,
        registry: RegistryClient | None = None,
        *,
        binary: str = "buildctl",
        run: Runner | None = None,
    ) -> None:
        super().__init__(registry, run=run)
        self.address = address
        self.binary = binary

    async def _build(self, request: BuildRequest, root: Path) -> tuple[str, str]:
        metadata = root / "metadata.json"
        log = await self._check(
            [
                self.binary,
                "--addr",
                self.address,
                "build",
                "--frontend",
                "dockerfile.v0",
                "--local",
                f"context={root / 'context'}",
                "--local",
                f"dockerfile={root / 'dockerfile'}",
                "--output",
                f"type=image,name={request.reference},push=true",
                "--metadata-file",
                str(metadata),
            ],
            "the build",
        )
        try:
            digest = str(json.loads(metadata.read_text(encoding="utf-8"))["containerimage.digest"])
        except (OSError, ValueError, KeyError) as exc:
            raise BuildError(f"buildctl wrote no image digest: {exc}") from exc
        return digest, log


class LocalDockerBuilder(_CommandBuilder):
    """`docker build`, `docker push`, then the digest the registry gave the push."""

    name = "local_docker"

    def __init__(
        self, registry: RegistryClient | None = None, *, binary: str = "docker", run: Runner | None = None
    ) -> None:
        super().__init__(registry, run=run)
        self.binary = binary

    async def _build(self, request: BuildRequest, root: Path) -> tuple[str, str]:
        dockerfile = root / "dockerfile" / "Dockerfile"
        log = await self._check(
            [
                self.binary,
                "build",
                "--file",
                str(dockerfile),
                "--tag",
                request.reference,
                str(root / "context"),
            ],
            "docker build",
        )
        log += await self._check([self.binary, "push", request.reference], "docker push")
        inspected = await self._check(
            [self.binary, "image", "inspect", "--format", "{{json .RepoDigests}}", request.reference],
            "docker image inspect",
        )
        try:
            digests = json.loads(inspected.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise BuildError(f"docker gave no repository digest: {inspected[-300:]}") from exc
        for entry in digests:
            name, _, digest = str(entry).partition("@")
            if name == request.repository and digest.startswith("sha256:"):
                return digest, log
        raise BuildError(f"the push gave `{request.repository}` no digest: {digests}")
