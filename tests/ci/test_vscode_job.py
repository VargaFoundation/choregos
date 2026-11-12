"""The editor extension is built and tested in CI like the console (ADR 0155, S68-01).

A second TypeScript application is only checked if a job runs it: the job must run on its own changes and on
the contract's generated types it is typed by, run lint, typecheck, the suite with its coverage floor and the
packaging, keep the VSIX, and be one of the jobs the gate waits for.
"""

from __future__ import annotations

import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
APP = ROOT / "apps" / "vscode"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _filters() -> dict:
    (step,) = [s for s in _workflow()["jobs"]["changes"]["steps"] if s.get("id") == "filter"]
    return yaml.safe_load(step["with"]["filters"])


def test_the_job_runs_on_the_extension_and_on_the_contract_it_is_typed_by() -> None:
    assert set(_filters()["vscode"]) >= {"apps/vscode/**", "packages/contracts/ts/**"}
    job = _workflow()["jobs"]["vscode"]
    assert job["if"] == "needs.changes.outputs.vscode == 'true'"
    assert _workflow()["jobs"]["changes"]["outputs"]["vscode"] == "${{ steps.filter.outputs.vscode }}"


def test_the_job_lints_typechecks_tests_and_packages() -> None:
    runs = [s["run"] for s in _workflow()["jobs"]["vscode"]["steps"] if "run" in s]
    assert runs == [
        "pnpm install --frozen-lockfile",
        "pnpm lint",
        "pnpm typecheck",
        "pnpm test",
        "pnpm package",
    ]
    (upload,) = [s for s in _workflow()["jobs"]["vscode"]["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload["with"]["path"] == "apps/vscode/dist/choregos-vscode.vsix"


def test_the_gate_waits_for_it() -> None:
    assert "vscode" in _workflow()["jobs"]["ci-ok"]["needs"]


def test_the_suite_has_a_coverage_floor_and_the_lint_checks_formatting() -> None:
    config = (APP / "vitest.config.ts").read_text(encoding="utf-8")
    scripts = json.loads((APP / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "thresholds" in config
    assert "--coverage" in scripts["test"]
    assert "prettier --check" in scripts["lint"]
    assert "--max-warnings 0" in scripts["lint"]


def test_the_extension_has_no_runtime_dependency() -> None:
    """The VSIX holds one bundle: a runtime dependency would be bundled unreviewed (or missed with
    `--no-dependencies`)."""
    manifest = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert "dependencies" not in manifest
