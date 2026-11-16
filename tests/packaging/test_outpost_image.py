"""Is the outpost daemon shipped as its own image, and kept out of the platform's? (S65-15, ADR 0147)

The daemon `choregos-outpost` runs in someone else's network. Its image carries the daemon and what
it imports, nothing else of the repository; it listens on nothing; and it is built, signed and given
an SBOM by the release like its neighbours. The API serves the daemon's door and never imports the
daemon: the API image does not install it.

Building the image is the CI's job (`images`), and the build itself checks that the daemon starts
and finds its contract; these tests keep a later edit from undoing the lines that say so.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import yaml
from choregos_core.publishing import PUBLISHING

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "outpost.Dockerfile"
PACKAGE = "choregos-outpost"


def _stages() -> dict[str, str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    parts = re.split(r"^FROM \S+ AS (\w+)$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _instructions(stage: str) -> list[str]:
    """The stage's instructions, comments left out and continued lines joined."""
    lines = [line for line in stage.splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines)).splitlines()


def test_the_package_is_a_workspace_member_with_its_command() -> None:
    project = tomllib.loads((ROOT / "packages/outpost/pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == PACKAGE and project["scripts"] == {PACKAGE: "choregos_outpost.cli:main"}
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert PACKAGE in workspace["project"]["dependencies"]
    assert workspace["tool"]["uv"]["sources"][PACKAGE] == {"workspace": True}
    assert "packages/outpost/src" in workspace["tool"]["mypy"]["mypy_path"].split(":")
    assert "packages/outpost/src" in workspace["tool"]["coverage"]["run"]["source"]
    assert project["version"] == workspace["project"]["version"], "one version everywhere (`bump_version.py`)"


def test_the_image_carries_the_daemon_and_nothing_else_of_the_repository() -> None:
    stages = _stages()
    assert list(stages) == ["builder", "runtime"]
    sync = next(line for line in _instructions(stages["builder"]) if "uv sync" in line)
    # Only the daemon's own closure, installed into the environment and not linked to the source tree.
    for flag in (f"--package {PACKAGE}", "--no-editable", "--frozen", "--no-dev"):
        assert flag in sync, flag
    assert "--all-packages" not in sync
    copied = [line for line in _instructions(stages["runtime"]) if line.startswith("COPY")]
    assert copied == ["COPY --from=builder --chown=1000:1000 /app/.venv /app/.venv"]
    assert "test ! -e /app/packages" in stages["runtime"], "the build no longer checks the tree is absent"


def test_the_image_listens_on_nothing_and_runs_as_the_daemon() -> None:
    runtime = _instructions(_stages()["runtime"])
    assert not [line for line in runtime if line.startswith(("EXPOSE", "HEALTHCHECK"))], (
        "an outpost only dials out (ADR 0147 §1): its image announces no port and probes none"
    )
    assert "USER 1000" in runtime
    assert runtime[-2:] == ['ENTRYPOINT ["/usr/bin/tini", "--", "choregos-outpost"]', 'CMD ["run"]']
    # The daemon must start in the image and find its contract: the build fails otherwise.
    checks = " ".join(line for line in runtime if line.startswith("RUN"))
    assert "choregos-outpost --version" in checks and "load_outpost_door()" in checks
    # Nothing of the operator's is baked in: no secret, no address, no policy.
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"^(ARG|ENV)\b.*(TOKEN|SECRET|KEY|PASSWORD|CONTROL_PLANE)", text, re.M | re.I)


def test_the_api_image_does_not_install_the_daemon() -> None:
    """The door is the API's; the daemon is not. The API image syncs the whole workspace but for the
    packages it does not run."""
    text = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    syncs = [line for line in re.sub(r"\\\n\s*", " ", text).splitlines() if line.startswith("RUN uv sync")]
    assert len(syncs) == 1 and "--all-packages" in syncs[0]
    assert f"--no-install-package {PACKAGE}" in syncs[0], "the API image would ship the outpost daemon"
    assert "--no-install-package choregos-agent-sdk" in syncs[0]
    # And no other image of the platform installs it: each syncs its own package.
    for name in ("tools.Dockerfile", "runner.Dockerfile"):
        other = (ROOT / "docker" / name).read_text(encoding="utf-8")
        assert PACKAGE not in other, name


def test_the_release_builds_signs_and_attests_the_image_as_its_neighbours() -> None:
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    images, manifest = release["jobs"]["images"], release["jobs"]["manifest"]
    matrix = images["strategy"]["matrix"]
    assert "outpost" in matrix["image"] and "tools" in matrix["image"]
    assert {"image": "outpost", "file": "docker/outpost.Dockerfile", "target": ""} in matrix["include"]
    build = next(step for step in images["steps"] if step.get("id") == "build")
    # One build step for every image of the matrix: an SBOM and max-mode provenance for each.
    assert build["with"]["file"] == "${{ matrix.file }}" and build["with"]["sbom"] is True
    assert build["with"]["provenance"] == "mode=max"
    assert manifest["strategy"]["matrix"]["image"] == matrix["image"], "an image built and never signed"
    signed = [step.get("run", "") for step in manifest["steps"]]
    assert any('cosign sign --yes "${IMAGE}@${digest}"' in run for run in signed)
    assert images["env"]["IMAGE"] == PUBLISHING.image("choregos-${{ matrix.image }}")


def test_the_ci_builds_and_scans_the_image_on_pull_requests() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    built = ci["jobs"]["images"]["strategy"]["matrix"]["include"]
    assert {"image": "outpost", "file": "docker/outpost.Dockerfile"} in built
    assert any("trivy" in str(step.get("uses", "")) for step in ci["jobs"]["images"]["steps"])
