# SPDX-License-Identifier: Apache-2.0
"""S45-32: the trials (`trials/`) are English, and every path into them resolves.

`tools/check_english.py` counts French prose, French declared names and French path components in
every tracked file. This test runs its tree scan over `trials/` and refuses any finding, so a French
comment, a French script name or a French folder cannot come back without the test turning red.
The recorded data files (`graphify/*.json`, `no-secret/runtime/Cargo.lock`,
`investment/sources-2026-10-14.json`) are scanned too: their content is kept as it was recorded, and
none of it reads as French.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: The trials with a results page. `dev-platform` has a README instead; `demo` and `knowledge` are tools.
TRIALS = (
    "claude-integration",
    "dev-project",
    "graphify",
    "hr",
    "investment",
    "lakehouse",
    "local-start",
    "no-secret",
    "ontology",
    "python",
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_trials", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_trials_have_no_french() -> None:
    checker = _load_checker()
    findings = checker.scan_tree(("trials/",))
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_the_trials_directory_replaced_the_french_one() -> None:
    former = "es" + "sai"  # the French name, spelled in two pieces so this line is not a path
    assert (ROOT / "trials").is_dir()
    assert not (ROOT / former).exists()
    assert not (ROOT / "tests" / former).exists()


def test_every_trial_has_its_results_page_and_a_paragraph_in_the_index() -> None:
    index = (ROOT / "trials" / "README.md").read_text(encoding="utf-8")
    for name in TRIALS:
        assert (ROOT / "trials" / name / "RESULTS.md").is_file(), name
    for name in (*TRIALS, "dev-platform", "demo", "knowledge"):
        assert f"`trials/{name}/`" in index, name
    assert not list((ROOT / "trials").rglob("RESULTATS.md"))


def test_the_paths_the_trials_name_resolve() -> None:
    for path in (
        "trials/local-start/run.sh",
        "trials/local-start/scenario.py",
        "trials/local-start/scenery.py",
        "trials/local-start/extension.Dockerfile",
        "trials/local-start/initdb/01-application-role.sql",
        "trials/hr/scenario_hr.py",
        "trials/demo/prepare_the_demo.py",
        "trials/lakehouse/trial.py",
        "trials/lakehouse/scope.py",
        "trials/dev-platform/infra_analysis.md",
        "trials/knowledge/example-kb.yaml",
        "tests/trials/test_scenario_dev.py",
    ):
        assert (ROOT / path).is_file(), path
