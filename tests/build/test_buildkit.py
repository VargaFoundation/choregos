"""A real `buildkitd` builds the reference zip (S41-13, ADR 0080 §9): pushed, found again under its
key, and built a second time from an empty cache to the same digest.

The unit tests (`packages/adapters/tests/test_image_builders.py`) prove the argv; this proves that
the argv does what it says on a real daemon and a real registry. It runs in the `hosted-build` CI
job, against the rootless image the chart deploys, and skips without:

- `CHOREGOS_TEST_BUILDKIT_ADDRESS`: the daemon, as `buildctl --addr` takes it;
- `CHOREGOS_TEST_BUILD_REPOSITORY`: where to push, `localhost:5000/choregos-builds/reference`.

`CHOREGOS_TEST_BUILT_IMAGE_FILE`, when set, receives the built `repository@sha256:…`: the job then
runs it through the hosted conformance suite, so the image proved stable is also an image that works.
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import zipfile

import pytest
from choregos_adapters.oci import BuildctlBuilder, BuildRequest, OciRegistryClient
from choregos_adapters.oci.builder import run_command
from choregos_api.schemas.agents import HostedRuntime
from choregos_api.services.hosted_sources import build_key, dockerfile, read_source

ROOT = pathlib.Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "examples" / "hosted" / "reference"
ADDRESS = os.environ.get("CHOREGOS_TEST_BUILDKIT_ADDRESS", "")
REPOSITORY = os.environ.get("CHOREGOS_TEST_BUILD_REPOSITORY", "")
BUILDCTL = os.environ.get("CHOREGOS_TEST_BUILDCTL", "buildctl")

pytestmark = [
    pytest.mark.buildkit,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (ADDRESS and REPOSITORY) or shutil.which(BUILDCTL) is None,
        reason="no buildkitd: set CHOREGOS_TEST_BUILDKIT_ADDRESS and CHOREGOS_TEST_BUILD_REPOSITORY",
    ),
]


def _reference_zip() -> bytes:
    """The reference agent as an author would zip it; fixed entry dates keep its digest fixed."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("agent.py", "responses_server.py"):
            archive.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), (REFERENCE / name).read_bytes())
    return buffer.getvalue()


def _request() -> BuildRequest:
    data = _reference_zip()
    archive = read_source(data)
    runtime = HostedRuntime.model_validate(
        {
            "kind": "hosted",
            "source": {
                "type": "zip",
                "sha256": archive.sha256,
                "runtime": "python_3_12",
                "entry_point": ["python", "responses_server.py"],
                "dependency_resolution": "bundled",
            },
        }
    )
    text = dockerfile(runtime, archive)
    return BuildRequest(build_key(text, archive), text, data, REPOSITORY)


async def test_the_reference_zip_is_built_once_and_rebuilt_from_nothing_to_the_same_digest() -> None:
    request = _request()
    registry = OciRegistryClient()
    builder = BuildctlBuilder(ADDRESS, registry, binary=BUILDCTL)

    first = await builder.build(request)
    assert first.image.startswith(f"{REPOSITORY}@sha256:")
    assert await registry.resolve(request.reference) == first.image.partition("@")[2], "pushed under its key"

    found = await builder.build(request)
    assert (found.image, found.built) == (first.image, False), "a key in the registry is not built again"

    # From nothing: no cache on the daemon, and no registry to say it is already there.
    code, output = await run_command([BUILDCTL, "--addr", ADDRESS, "prune", "--all"])
    assert code == 0, output
    rebuilt = await BuildctlBuilder(ADDRESS, None, binary=BUILDCTL).build(request)
    assert rebuilt.built, "built again, not found"
    assert rebuilt.image == first.image, (
        "the same zip on the same base image gives the same digest: without it, `already built` "
        "would only mean `built once on this daemon`"
    )

    if target := os.environ.get("CHOREGOS_TEST_BUILT_IMAGE_FILE"):
        pathlib.Path(target).write_text(first.image + "\n", encoding="utf-8")
