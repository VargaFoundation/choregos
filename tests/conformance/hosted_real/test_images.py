"""Every hosted example becomes an image that passes the eight checks, built for real (S41-23).

S41-06 and S41-20 proved the examples against fakes and as local processes; this builds them with
Docker and runs the image through `tests/conformance/hosted`:

- an example with a `Dockerfile` (the Python ones) is built with `docker build`, as a user would;
- a zip example (`choregos.json`, no Dockerfile: Node and .NET) goes through the platform's own
  path: the zip as an upload, the Dockerfile `dockerfile()` writes for it, and `LocalDockerBuilder`
  (`docker build`, `docker push` to a real registry, the digest the registry gave), built a second
  time to see the registry answer "already built" and no second build.

The image is then run with `docker run` by the conformance harness and must pass every check.
Skipped without a daemon (and, for the zips, a registry); the `hosted-images` CI job fails on a skip.
`CHOREGOS_TEST_HOSTED_EXAMPLES` (comma separated) narrows the run to some examples.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest
from choregos_adapters.oci import BuildRequest, LocalDockerBuilder, OciRegistryClient
from choregos_api.services.hosted_sources import build_key, dockerfile, read_source

from tools import hosted_dockerfile as tool

from ..hosted.harness import HOSTED_CHECKS, HostedAgentSpec, run_hosted_suite

pytestmark = pytest.mark.hosted_docker

EXAMPLES = pathlib.Path(__file__).resolve().parents[3] / "examples" / "hosted"
WANTED = {name for name in os.environ.get("CHOREGOS_TEST_HOSTED_EXAMPLES", "").split(",") if name}
ALL = sorted(
    path.name
    for path in EXAMPLES.iterdir()
    if path.is_dir() and ((path / "Dockerfile").exists() or (path / "choregos.json").exists())
)
NAMES = [name for name in ALL if not WANTED or name in WANTED]
BUILD_TIMEOUT_S = 1500
#: Containers and images this suite makes carry this prefix, so that a leftover is easy to find.
PREFIX = "s41-23-"


def test_every_example_is_covered() -> None:
    """A new example directory is built and run too: none is left out by forgetting a list."""
    assert len(ALL) == 11, ALL
    assert set(ALL) >= WANTED, (
        f"CHOREGOS_TEST_HOSTED_EXAMPLES names an example that does not exist: {WANTED - set(ALL)}"
    )


def build_dockerfile_example(name: str) -> str:
    tag = f"{PREFIX}{name}"
    subprocess.run(
        ["docker", "build", "--quiet", "--tag", tag, str(EXAMPLES / name)],
        check=True,
        capture_output=True,
        timeout=BUILD_TIMEOUT_S,
    )
    return tag


async def build_zip_example(name: str, registry: str) -> str:
    directory = EXAMPLES / name
    data = tool.zipped(directory)
    archive = read_source(data)
    text = dockerfile(tool.version(directory, archive), archive)
    request = BuildRequest(build_key(text, archive), text, data, f"{registry}/{PREFIX}{name}")
    builder = LocalDockerBuilder(OciRegistryClient())
    built = await builder.build(request)
    assert built.image.startswith(f"{request.repository}@sha256:")
    again = await builder.build(request)
    assert (again.image, again.built) == (built.image, False), "the registry has it under its key"
    return built.image


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NAMES)
async def test_an_example_is_built_and_passes_every_check(
    name: str, docker: str, request: pytest.FixtureRequest
) -> None:
    if (EXAMPLES / name / "choregos.json").exists():
        image = await build_zip_example(name, request.getfixturevalue("registry"))
    else:
        image = build_dockerfile_example(name)
    result = await run_hosted_suite(name, HostedAgentSpec(image=image), timeout_s=150)
    assert result.ok, result.failed
    assert set(result.passed) == set(HOSTED_CHECKS)
