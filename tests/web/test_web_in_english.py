"""S45-29: the console is English from end to end.

The last story of the console track (S45-26 to S45-29): the remaining tests, e2e specs and config
files are translated, the French `data-testid` values, data attributes and CSS classes are renamed,
the gestures fixture is `console-gestures.json`, and the TypeScript shims that kept the old French
paths and names alive are deleted. Four guards keep it that way:

- the console holds no French the checker (`tools/check_english.py`) can see, `README.md` apart;
- no `data-testid` literal of `apps/web/src` is a French identifier;
- no internal alias (`alias:internal`) is left in `apps/web`;
- none of the French names renamed here comes back.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
#: Written by S45-35, the last story of the track.
STILL_FRENCH = {"apps/web/README.md"}
SOURCE_SUFFIXES = {".ts", ".tsx", ".mts", ".css"}
SCANNED_FOLDERS = ("src", "tests", "e2e", "e2e-live")

TESTID_LITERAL = re.compile(r"""data-testid=(?:"([^"]*)"|\{`([^`]*)`\})""")

#: `data-testid` values, data attributes and CSS classes that were French and must not come back.
FORMER_NAMES = (
    # data attributes
    "data-donnee",
    "data-statut",
    "data-etat",
    "data-genre",
    "data-etape",
    "data-fois",
    "data-appel",
    "data-sortie",
    "data-titre",
    "data-lecture",
    "data-barre-du-haut",
    # css classes of the journey map
    "parcours-pouls",
    "parcours-trait",
    "parcours-trace",
    "parcours-anime",
    "parcours-forme",
    "parcours-clignote",
)
#: Former `data-testid` values that the identifier lexicon does not catch (a one-word French name).
FORMER_TESTIDS = (
    "apercu",
    "artefact-",
    "attentes-du-board",
    "epingles",
    "glossaire",
    "manque-",
    "mois",
    "recapitulatif",
    "registre",
    "regles",
    "revue",
    "risque",
    "secondaires-",
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_web_in_english", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sources(*folders: str) -> list[pathlib.Path]:
    return [
        path
        for folder in folders
        for path in sorted((WEB / folder).rglob("*"))
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and "node_modules" not in path.parts
    ]


def test_the_console_has_no_french() -> None:
    checker = _load_checker()
    findings = [f for f in checker.scan_tree(("apps/web/",)) if f.path not in STILL_FRENCH]
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_no_data_testid_is_french() -> None:
    checker = _load_checker()
    seen = 0
    french: list[str] = []
    for path in _sources("src"):
        for match in TESTID_LITERAL.finditer(path.read_text(encoding="utf-8")):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            seen += 1
            # A template's fixed parts only: `state-${id}` is judged as `state-`.
            fixed = re.sub(r"\$\{[^}]*\}", "-", value)
            reason = checker.code_identifier_reason(fixed)
            if reason:
                french.append(f"{path.relative_to(ROOT)}: {value!r}: {reason}")
    assert seen > 300, "the scan found too few data-testid literals"
    assert french == [], "\n".join(french)


def test_no_former_french_testid_comes_back() -> None:
    found: list[str] = []
    for path in _sources(*SCANNED_FOLDERS):
        text = path.read_text(encoding="utf-8")
        for former in FORMER_TESTIDS:
            if re.search(rf"""["'`]{re.escape(former)}(?:["'`]|\$\{{)""", text):
                found.append(f"{path.relative_to(ROOT)}: {former!r}")
    assert found == [], "\n".join(found)


def test_no_french_data_attribute_or_css_class_comes_back() -> None:
    found: list[str] = []
    for path in _sources(*SCANNED_FOLDERS):
        text = path.read_text(encoding="utf-8")
        for former in FORMER_NAMES:
            if re.search(rf"(?<![\w-]){re.escape(former)}(?![\w-])", text):
                found.append(f"{path.relative_to(ROOT)}: {former}")
    assert found == [], "\n".join(found)


def test_no_internal_ts_shim_remains() -> None:
    marked = [
        str(path.relative_to(ROOT))
        for path in _sources(*SCANNED_FOLDERS)
        if "alias:internal" in path.read_text(encoding="utf-8")
    ]
    assert marked == [], "\n".join(marked)
    former_paths = (
        "src/components/workflows/brouillon.tsx",
        "src/components/workflows/barre-du-brouillon.tsx",
        "src/components/workflows/panneaux.tsx",
        "src/components/workflows/renommer.ts",
        "src/components/workflows/texte-illisible.tsx",
        "src/components/garanties.tsx",
        "src/components/plan-de-l-agent.tsx",
        "src/components/geste-confirme.tsx",
        "src/components/agents/registre.ts",
        "src/components/coquille/navigation.tsx",
        "src/lib/activite-du-run.ts",
        "src/lib/sortes-de-connecteurs.ts",
        "src/lib/plan-de-l-agent.ts",
        "src/mocks/parcours.ts",
    )
    assert [p for p in former_paths if (WEB / p).exists()] == []


def test_the_gestures_fixture_and_the_e2e_specs_have_english_names() -> None:
    fixtures = WEB / "tests" / "fixtures"
    assert (fixtures / "console-gestures.json").is_file()
    assert not (fixtures / "gestes-de-la-console.json").exists()
    for spec in (
        "accessibility",
        "letter-case",
        "words-and-forms",
        "routing",
        "no-overflow",
        "security",
    ):
        assert (WEB / "e2e" / f"{spec}.spec.ts").is_file(), spec
    for former in (
        "accessibilite",
        "la-casse",
        "mots-et-formulaires",
        "routage",
        "sans-debordement",
        "securite",
    ):
        assert not (WEB / "e2e" / f"{former}.spec.ts").exists(), former
