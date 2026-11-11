"""The native CLI binaries in the release and in the CI (S62-07).

The release builds the five targets on runners of their own OS and architecture, smoke-tests each
where no Python is available, signs every file, attaches them to the GitHub release and puts them in
the API image under /opt/choregos/cli/; the CI builds the Linux x86_64 one on every pull request that
touches what goes into it. These tests read the workflows and the Dockerfile: a job dropped, a step
that no longer runs the smoke test, or an image built before its binaries turns them red.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = ROOT / "docker" / "api.Dockerfile"
BLOCKERS = ROOT / "docs" / "plan" / "BLOCKERS.md"
#: The fixed file names, shared with S62-08 (`tools/cli_binary/build.py` holds the same five).
NAMES = {
    "choregos-linux-x86_64",
    "choregos-linux-arm64",
    "choregos-macos-arm64",
    "choregos-macos-x86_64",
    "choregos-windows-x86_64.exe",
}
SIGNING_SECRETS = (
    "WINDOWS_CERTIFICATE_PFX_BASE64",
    "WINDOWS_CERTIFICATE_PASSWORD",
    "MACOS_CERTIFICATE_P12_BASE64",
    "MACOS_CERTIFICATE_PASSWORD",
    "MACOS_SIGNING_IDENTITY",
    "MACOS_NOTARY_APPLE_ID",
    "MACOS_NOTARY_TEAM_ID",
    "MACOS_NOTARY_PASSWORD",
)


def _jobs(path: pathlib.Path) -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict) -> str:  # type: ignore[type-arg]
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_the_release_builds_the_five_targets_on_native_runners() -> None:
    matrix = _jobs(RELEASE)["cli-binaries"]["strategy"]["matrix"]["include"]
    runners = {entry["name"]: entry["runner"] for entry in matrix}
    assert set(runners) == NAMES
    assert runners["choregos-linux-arm64"].endswith("-arm")
    assert not runners["choregos-linux-x86_64"].endswith("-arm")
    assert runners["choregos-macos-x86_64"].endswith("-intel")
    assert (
        runners["choregos-macos-arm64"].startswith("macos-")
        and "intel" not in runners["choregos-macos-arm64"]
    )
    assert runners["choregos-windows-x86_64.exe"].startswith("windows-")
    linux = [entry for entry in matrix if entry["name"].startswith("choregos-linux")]
    assert all(entry["build"] == "--manylinux" for entry in linux)
    assert all(entry["smoke"] == "--container debian:bookworm-slim" for entry in linux)


def test_each_release_binary_is_smoke_tested_with_the_tag_s_version() -> None:
    job = _jobs(RELEASE)["cli-binaries"]
    runs = _runs(job)
    assert "tools/cli_binary/build.py" in runs
    assert 'tools/cli_binary/smoke.py "dist/cli/${{ matrix.name }}"' in runs
    assert '--expect-version "${GITHUB_REF_NAME#v}" ${{ matrix.smoke }}' in runs
    smoke = next(s for s in job["steps"] if "smoke.py" in s.get("run", ""))
    assert "if" not in smoke and "continue-on-error" not in smoke
    names = [step.get("name", step.get("uses", "")) for step in job["steps"]]
    assert names.index("smoke test, where no Python is available") > names.index("build")


def test_signing_runs_only_when_its_secrets_exist_and_they_are_documented() -> None:
    job = _jobs(RELEASE)["cli-binaries"]
    text = RELEASE.read_text(encoding="utf-8")
    for secret in SIGNING_SECRETS:
        assert f"secrets.{secret}" in text, secret
    for key in ("HAS_WINDOWS_CERTIFICATE", "HAS_MACOS_CERTIFICATE", "HAS_MACOS_NOTARY"):
        assert re.fullmatch(r"\$\{\{ secrets\.\w+ != '' \}\}", job["env"][key]), key
    conditional = {s["name"]: s["if"] for s in job["steps"] if "if" in s and "env.HAS_" in s["if"]}
    assert any("signtool" in s.get("run", "") for s in job["steps"] if s.get("name") in conditional)
    assert any("notarytool" in s.get("run", "") for s in job["steps"] if s.get("name") in conditional)
    blockers = BLOCKERS.read_text(encoding="utf-8")
    missing = [secret for secret in SIGNING_SECRETS if secret not in blockers]
    assert not missing, f"BLOCKERS.md does not name {missing}"


def test_the_release_signs_every_file_and_writes_the_manifest() -> None:
    job = _jobs(RELEASE)["cli-release"]
    assert job["needs"] == "cli-binaries"
    assert job["permissions"]["id-token"] == "write"
    runs = _runs(job)
    assert 'tools/cli_binary/release.py dist/cli --version "${GITHUB_REF_NAME#v}"' in runs
    assert "--partial" not in runs
    assert "for file in choregos-* SHA256SUMS manifest.json; do" in runs
    assert 'cosign sign-blob --yes --bundle "$file.sigstore.json" "$file"' in runs
    assert "cosign verify-blob" in runs


def test_the_images_wait_for_the_binaries_and_the_api_image_carries_them() -> None:
    jobs = _jobs(RELEASE)
    assert jobs["images"]["needs"] == "cli-release"
    build_step = next(s for s in jobs["images"]["steps"] if s.get("id") == "build")
    assert build_step["with"]["build-contexts"] == "${{ matrix.image == 'api' && 'cli=dist/cli' || '' }}"
    download = next(
        s for s in jobs["images"]["steps"] if s.get("uses", "").startswith("actions/download-artifact")
    )
    assert download["with"]["name"] == "cli-dist" and download["if"] == "matrix.image == 'api'"
    assert "sha256sum -c SHA256SUMS" in _runs(jobs["images"])


def test_the_github_release_attaches_the_binaries() -> None:
    job = _jobs(RELEASE)["changelog"]
    assert "cli-release" in job["needs"]
    publish = next(s for s in job["steps"] if s.get("uses", "").startswith("softprops/action-gh-release"))
    assert "dist/cli/*" in publish["with"]["files"].split()


def test_the_api_dockerfile_copies_the_binaries_only_when_given() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM scratch AS cli$", text, re.M)
    api = text[text.index("FROM commun AS api") : text.index("FROM commun AS worker-local")]
    assert "RUN --mount=type=bind,from=cli,target=/mnt/cli" in api
    assert "if [ -f /mnt/cli/manifest.json ]; then" in api
    assert "cp /mnt/cli/* /opt/choregos/cli/" in api
    assert api.rstrip().count("USER 1000") >= 1


def test_the_ci_builds_and_smoke_tests_the_linux_binary_on_pull_requests() -> None:
    jobs = _jobs(CI)
    job = jobs["cli-binary"]
    assert job["if"] == "needs.changes.outputs.cli-binary == 'true' || github.ref == 'refs/heads/main'"
    runs = _runs(job)
    assert "python3 tools/cli_binary/build.py --manylinux" in runs
    assert "tools/cli_binary/smoke.py dist/cli/choregos-linux-x86_64" in runs
    assert "--container debian:bookworm-slim" in runs
    cache = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/cache"))
    assert cache["with"]["path"] == "build/cli-binary/cache"
    filters = yaml.safe_load(
        next(s for s in jobs["changes"]["steps"] if s.get("id") == "filter")["with"]["filters"]
    )
    assert {"packages/cli/**", "packages/core/**", "packages/contracts/**", "tools/cli_binary/**"} <= set(
        filters["cli-binary"]
    )
    assert "cli-binary" in jobs["ci-ok"]["needs"]
