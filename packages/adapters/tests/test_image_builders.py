"""The builders of hosted agents' images (S41-08, ADR 0080 §9): one contract for BuildKit and for
Docker, a key built once, and a failure that says why.

The commands are scripted: what is proved is the argv each builder runs, how it reads the digest,
and that it runs nothing for a key already in the registry. A real `buildkitd` is S41-13.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from choregos_adapters.fakes import FakeImageBuilder, FakeRegistryClient
from choregos_adapters.oci import BuildctlBuilder, BuildError, BuildRequest, LocalDockerBuilder

KEY = "sha256:" + "b" * 64
REPOSITORY = "registry.internal/choregos-builds/varga"
DIGEST = "sha256:" + "d" * 64
DOCKERFILE = "FROM python:3.13-slim@sha256:" + "a" * 64 + "\nCOPY . /app\n"


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _request(**files: str) -> BuildRequest:
    return BuildRequest(KEY, DOCKERFILE, _zip(files or {"main.py": "print(1)"}), REPOSITORY)


class Script:
    """A command runner that answers each command in turn, and sees the build context as it is."""

    def __init__(self, *answers: tuple[int, str], digest: str | None = DIGEST) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.context: dict[str, str] = {}
        self.dockerfile = ""
        self.digest = digest

    async def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        for argument in argv:
            for prefix in ("context=", "dockerfile="):
                if argument.startswith(prefix):
                    self._read(prefix, Path(argument.removeprefix(prefix)))
        if "--metadata-file" in argv and self.digest is not None:
            metadata = Path(argv[argv.index("--metadata-file") + 1])
            metadata.write_text(json.dumps({"containerimage.digest": self.digest}), encoding="utf-8")
        if "build" in argv and "--file" in argv:
            self.dockerfile = Path(argv[argv.index("--file") + 1]).read_text(encoding="utf-8")
            self._read("context=", Path(argv[-1]))
        return self.answers.pop(0)

    def _read(self, prefix: str, path: Path) -> None:
        if prefix == "dockerfile=":
            self.dockerfile = (path / "Dockerfile").read_text(encoding="utf-8")
        else:
            self.context = {
                str(p.relative_to(path)): p.read_text(encoding="utf-8")
                for p in path.rglob("*")
                if p.is_file()
            }


async def test_buildkit_builds_the_zip_with_the_platforms_dockerfile_and_pushes_it() -> None:
    script = Script((0, "#1 DONE"))
    builder = BuildctlBuilder("tcp://choregos-buildkitd:1234", FakeRegistryClient(strict=True), run=script)
    result = await builder.build(_request(**{"main.py": "print(1)", "pkg/util.py": "x = 1"}))
    assert result.image == f"{REPOSITORY}@{DIGEST}" and result.built and result.builder == "buildkit"
    (argv,) = script.calls
    assert argv[:4] == ["buildctl", "--addr", "tcp://choregos-buildkitd:1234", "build"]
    assert f"type=image,name={REPOSITORY}:build-{'b' * 64},push=true" in argv
    assert script.context == {"main.py": "print(1)", "pkg/util.py": "x = 1"}
    assert script.dockerfile == DOCKERFILE, "the Dockerfile is the platform's, outside the context"


async def test_local_docker_builds_pushes_and_reads_the_repository_digest() -> None:
    inspect = json.dumps([f"other.example/x@sha256:{'0' * 64}", f"{REPOSITORY}@{DIGEST}"])
    script = Script((0, "built"), (0, "pushed"), (0, inspect + "\n"))
    builder = LocalDockerBuilder(FakeRegistryClient(strict=True), run=script)
    result = await builder.build(_request())
    assert result.image == f"{REPOSITORY}@{DIGEST}" and result.builder == "local_docker"
    reference = f"{REPOSITORY}:build-{'b' * 64}"
    assert [c[:2] for c in script.calls] == [["docker", "build"], ["docker", "push"], ["docker", "image"]]
    assert script.calls[1] == ["docker", "push", reference]
    assert script.context == {"main.py": "print(1)"} and script.dockerfile == DOCKERFILE


@pytest.mark.parametrize("builder", ["buildkit", "local_docker"])
async def test_a_key_already_in_the_registry_is_not_built_again(builder: str) -> None:
    """A replayed activity, a second version of the same zip: the image is found, nothing runs."""
    registry = FakeRegistryClient(strict=True)
    pushed = registry.push(f"{REPOSITORY}:build-{'b' * 64}", "the image built before")
    script = Script()
    chosen = (
        BuildctlBuilder("tcp://b:1234", registry, run=script)
        if builder == "buildkit"
        else LocalDockerBuilder(registry, run=script)
    )
    result = await chosen.build(_request())
    assert script.calls == [], "nothing was built"
    assert result.image == f"{REPOSITORY}@{pushed}" and not result.built


async def test_a_failing_build_says_why_with_the_end_of_its_log() -> None:
    log = "step 1\n" * 2000 + "ERROR: No matching distribution found for httpx==99"
    builder = BuildctlBuilder("tcp://b:1234", FakeRegistryClient(strict=True), run=Script((1, log)))
    with pytest.raises(BuildError, match="No matching distribution") as raised:
        await builder.build(_request())
    assert len(str(raised.value)) < 4200, "the log is cut to its end"


async def test_a_build_without_a_digest_fails_rather_than_guess() -> None:
    no_metadata = BuildctlBuilder("tcp://b:1234", run=Script((0, "done"), digest=None))
    with pytest.raises(BuildError, match="no image digest"):
        await no_metadata.build(_request())
    other_repository = json.dumps([f"elsewhere.example/x@{DIGEST}"])
    docker = LocalDockerBuilder(run=Script((0, ""), (0, ""), (0, other_repository)))
    with pytest.raises(BuildError, match="no digest"):
        await docker.build(_request())


async def test_a_context_entry_outside_the_build_directory_is_refused_before_any_command() -> None:
    script = Script((0, ""))
    with pytest.raises(BuildError, match="outside the build context"):
        await BuildctlBuilder("tcp://b:1234", run=script).build(_request(**{"../escape.py": "x"}))
    assert script.calls == []


async def test_the_fake_builds_a_key_once_and_fails_on_demand() -> None:
    fake = FakeImageBuilder()
    first = await fake.build(_request())
    again = await fake.build(_request())
    assert first.built and not again.built and first.image == again.image
    assert fake.builds == [KEY] and len(fake.requests) == 2
    fake.fail("sha256:" + "c" * 64, "pip install: no matching distribution")
    with pytest.raises(BuildError, match="no matching distribution"):
        await fake.build(BuildRequest("sha256:" + "c" * 64, DOCKERFILE, _zip({"a": ""}), REPOSITORY))
