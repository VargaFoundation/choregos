# SPDX-License-Identifier: Apache-2.0
"""The phone app is built and tested in CI like the console and the editor (ADR 0193, S81-07).

A third application is only checked if a job runs it: the job must run on its own changes and on the REST
contract its Kotlin types are written against, run Android lint, the unit suite and the release build, keep
the APK, and be one of the jobs the gate waits for. The release's signing is the build file's: it signs
only when the operator's four variables are all present, and never with a debug key.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
APP = ROOT / "apps" / "android"


def _workflow() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _filters() -> dict:  # type: ignore[type-arg]
    (step,) = [s for s in _workflow()["jobs"]["changes"]["steps"] if s.get("id") == "filter"]
    return yaml.safe_load(step["with"]["filters"])


def test_the_job_runs_on_the_app_and_on_the_contract_it_is_written_against() -> None:
    assert set(_filters()["android"]) >= {"apps/android/**", "packages/contracts/openapi.yaml"}
    job = _workflow()["jobs"]["android"]
    assert job["if"] == "needs.changes.outputs.android == 'true'"
    assert _workflow()["jobs"]["changes"]["outputs"]["android"] == "${{ steps.filter.outputs.android }}"


def test_the_job_lints_tests_and_builds_the_release_with_the_pinned_toolchain() -> None:
    steps = _workflow()["jobs"]["android"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert runs == ["./gradlew --no-daemon lint test assembleRelease"]
    (java,) = [s for s in steps if s.get("uses", "").startswith("actions/setup-java@")]
    assert java["with"]["java-version"] == 17
    assert any(s.get("uses", "").startswith("gradle/actions/setup-gradle@") for s in steps)
    uploads = [s for s in steps if "upload-artifact" in s.get("uses", "")]
    assert any(u["with"]["path"].endswith("outputs/apk/release/*.apk") for u in uploads)


def test_the_gate_waits_for_it() -> None:
    assert "android" in _workflow()["jobs"]["ci-ok"]["needs"]


def test_the_make_target_runs_the_same_three_tasks() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "cd apps/android && ./gradlew lint test assembleRelease" in makefile


def test_the_wrapper_is_pinned_to_a_checksum() -> None:
    props = (APP / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(encoding="utf-8")
    assert "distributionSha256Sum=" in props
    assert "-bin.zip" in props


def test_the_release_signs_only_with_the_operators_variables_and_never_with_a_debug_key() -> None:
    build = (APP / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    for variable in (
        "ANDROID_KEYSTORE_BASE64",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_ALIAS",
        "ANDROID_KEY_PASSWORD",
    ):
        assert f'System.getenv("{variable}")' in build
    assert 'signingConfigs.getByName("debug")' not in build
    assert not list(APP.rglob("*.jks")) and not list(APP.rglob("*.keystore"))
