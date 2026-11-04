"""S45-26: the console's lib, mocks, types and proxy are English.

`tools/check_english.py` counts French prose, French declared names and French path components in
every tracked file. This test runs its tree scan over the files this story renamed and translated
(`apps/web/src/lib`, `src/mocks` except the journey fixtures, `src/types`, `src/proxy.ts`) and refuses any
finding, so a French comment or a French local name cannot come back without the test turning red.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIXES = (
    "apps/web/src/lib/",
    "apps/web/src/mocks/",
    "apps/web/src/types/",
    "apps/web/src/proxy.ts",
)
#: Nothing is excluded since S45-27 renamed the journey fixtures (`mocks/journey.ts`); the former
#: path is a shim, which declares itself with `lang: fr-ok-file`.
EXCLUDED: tuple[str, ...] = ()


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_web_lib", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_lib_mocks_types_have_no_french() -> None:
    checker = _load_checker()
    findings = [f for f in checker.scan_tree(PREFIXES) if f.path not in EXCLUDED]
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_the_renamed_lib_modules_have_english_paths() -> None:
    lib = ROOT / "apps" / "web" / "src" / "lib"
    for name in ("run-activity.ts", "connector-kinds.ts", "agent-plan.ts"):
        assert (lib / name).is_file()
