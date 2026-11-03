"""S45-27: the work-item journey and the workflow map are English.

`tools/check_english.py` counts French prose, French declared names and French path components in
every tracked file. This test runs its tree scan over what this story renamed and translated (the
journey components, the workflow components, the journey fixtures, their tests and e2e specs) and
refuses any finding, so a French comment, a French local name or a French file name cannot come back
without the test turning red. The former paths stay as shims that declare themselves
(`lang: fr-ok-file`), which the scan skips.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = "apps/web/"
PREFIXES = (
    f"{WEB}src/components/journey/",
    f"{WEB}src/components/workflows/",
    f"{WEB}src/mocks/journey.ts",
    f"{WEB}tests/journey-map.test.tsx",
    f"{WEB}tests/journey-model.test.ts",
    f"{WEB}tests/map-layout.test.ts",
    f"{WEB}tests/map-workflow-map.test.tsx",
    f"{WEB}tests/map-snake.test.tsx",
    f"{WEB}tests/map-colours.test.ts",
    f"{WEB}tests/fields.test.ts",
    f"{WEB}tests/rename.test.ts",
    f"{WEB}tests/new-workflow.test.tsx",
    f"{WEB}tests/draft-storage-key.test.ts",
    f"{WEB}tests/workflow-edition.test.tsx",
    f"{WEB}tests/workflow-draft-quick-gestures.test.tsx",
    f"{WEB}tests/step-details.test.tsx",
    f"{WEB}e2e/map.spec.ts",
    f"{WEB}e2e/journey.spec.ts",
    f"{WEB}e2e/yaml-editor.spec.ts",
)
#: The former paths, each a shim until the one story that removes them (S45-29).
SHIMS = (
    "src/components/parcours/carte-du-parcours.tsx",
    "src/components/parcours/modele.ts",
    "src/components/workflows/carte/carte-du-workflow.tsx",
    "src/components/workflows/carte/disposition.ts",
    "src/components/workflows/brouillon.tsx",
    "src/components/workflows/barre-du-brouillon.tsx",
    "src/components/workflows/panneaux.tsx",
    "src/components/workflows/renommer.ts",
    "src/components/workflows/texte-illisible.tsx",
    "src/mocks/parcours.ts",
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_web_journey", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_journey_and_workflow_components_have_no_french() -> None:
    checker = _load_checker()
    findings = checker.scan_tree(PREFIXES)
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_the_renamed_modules_have_english_paths() -> None:
    components = ROOT / WEB / "src" / "components"
    for name in ("journey-map.tsx", "colours.ts", "layout.ts", "reader.ts", "model.ts", "step-panel.tsx"):
        assert (components / "journey" / name).is_file(), name
    for name in ("workflow-map.tsx", "layout.ts"):
        assert (components / "workflows" / "map" / name).is_file(), name
    for name in ("draft-bar.tsx", "draft.tsx", "fields.ts", "panels.tsx", "rename.ts", "unreadable-text.tsx"):
        assert (components / "workflows" / name).is_file(), name
    assert (ROOT / WEB / "src" / "mocks" / "journey.ts").is_file()


def test_the_former_paths_are_shims_that_say_so() -> None:
    """A former path left in place must declare itself, or the scan would count its French."""
    for relative in SHIMS:
        text = (ROOT / WEB / relative).read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:5])
        assert "lang: fr-ok-file" in head, relative
        assert len(text.splitlines()) < 60, f"{relative} is more than a shim"


def test_the_french_edge_label_fallback_is_gone() -> None:
    source = (ROOT / WEB / "src/components/workflows/map/layout.ts").read_text(encoding="utf-8")
    assert "échec" not in source and "rejet" not in source  # lang: fr-ok alias:external
