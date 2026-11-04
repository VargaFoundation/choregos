"""S45-28: the console's pages, shell and components are English.

`tools/check_english.py` counts French prose, French declared names and French path components in
every tracked file. This test runs its tree scan over what this story renamed and translated:
`apps/web/src/app`, every component outside the journey and workflows folders (owned by S45-27),
the vitest files whose first `@/` import is one of those, and the five e2e specs that follow the
renamed components. A French comment, test title or local name cannot come back without the test
turning red.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"

#: Components owned by another story (the journey and the workflows), before and after its renames.
OTHER_STORIES = (
    "apps/web/src/components/parcours/",
    "apps/web/src/components/journey/",
    "apps/web/src/components/workflows/",
)
#: Compatibility shims keep their old path and French name on purpose (`lang: fr-ok-file`).
E2E_SPECS = (
    "activity.spec.ts",
    "shell.spec.ts",
    "connector-secrets.spec.ts",
    "spending.spec.ts",
    "irreversible-gestures.spec.ts",
)
FIRST_ALIAS_IMPORT = re.compile(r"""(?:from\s+|import\(\s*|mock\(\s*)["'](@/[^"']+)["']""")
#: Files whose first `@/` import is ours but that another story owns (a lib story file, the fixtures).
EXCLUDED = ("apps/web/tests/fixtures/",)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_web_app", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _owned_tests() -> list[str]:
    owned = []
    for path in sorted((WEB / "tests").glob("*.test.ts*")):
        match = FIRST_ALIAS_IMPORT.search(path.read_text(encoding="utf-8"))
        if match is None:
            continue
        target = match.group(1)
        if target.startswith("@/app/") or (
            target.startswith("@/components/")
            and not target.startswith(
                ("@/components/parcours/", "@/components/journey/", "@/components/workflows/")
            )
        ):
            owned.append(f"apps/web/tests/{path.name}")
    return owned


def _is_owned(path: str, tests: set[str]) -> bool:
    if path.startswith(EXCLUDED) or path.startswith(OTHER_STORIES):
        return False
    if path.startswith(("apps/web/src/app/", "apps/web/src/components/")):
        return True
    if path.startswith("apps/web/e2e/"):
        return path.removeprefix("apps/web/e2e/") in E2E_SPECS
    return path in tests


def test_pages_and_components_have_no_french() -> None:
    checker = _load_checker()
    tests = set(_owned_tests())
    assert len(tests) > 30, "the first-import rule found too few vitest files"
    findings = [f for f in checker.scan_tree(("apps/web/",)) if _is_owned(f.path, tests)]
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_the_renamed_files_have_english_paths() -> None:
    components = WEB / "src" / "components"
    for name in (
        "access.tsx",
        "actions/ontology.tsx",
        "actions/status.tsx",
        "run-activity.tsx",
        "theme-picker.tsx",
        "org-connectors.tsx",
        "connectors.tsx",
        "shell/sidebar.tsx",
        "shell/navigation.tsx",
        "discovery.ts",
        "guarantees.tsx",
        "confirmed-gesture.tsx",
        "cost-graph.tsx",
        "project-operations.tsx",
        "agent-plan.tsx",
        "evidence.tsx",
        "skills/tabs.tsx",
    ):
        assert (components / name).is_file(), name
    assert (WEB / "src" / "app" / "document-title.tsx").is_file()
    for spec in E2E_SPECS:
        assert (WEB / "e2e" / spec).is_file(), spec
    assert not (components / "coquille" / "barre-laterale.tsx").exists()
