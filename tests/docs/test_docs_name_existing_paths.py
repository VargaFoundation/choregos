# SPDX-License-Identifier: Apache-2.0
"""S45-44: the documentation names repository paths that exist, in English, and the upgrade notes are folded.

After the English rename (ADR 0098) a page that still names a former French test folder or chart template
sends the reader to a path that is gone. This test reads every backticked token of the pages below that
starts with a repository directory and refuses one that is not in the tree, unless it is in
`HISTORICAL` with its reason. The upgrade-note fragments of the rename are folded into
`docs/deployment.md` ("Renamed in 0.21") and the folder is deleted.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
PREFIXES = ("apps/", "packages/", "tests/", "charts/", "infra/", "docker/", "tools/", "trials/", "demo/")
BACKTICKED = re.compile(r"`([^`\n]+)`")
#: A path may carry a line, a test id or an anchor after it: `a/b.py:12`, `a/b.py::test_x`, `a/b.md#h`.
SUFFIX = re.compile(r"[:#].*$")

#: Pages that are not read. `docs/procedures` is not listed at all: it is generated from
#: `choregos_core/procedures` and refused on drift by `tests/procedures/test_export_docs.py`. The
#: plan pages below are dated records that quote the paths of the day (a story that has since
#: renamed a file does not edit the plan that named it): STATUS is the ledger of every merged story,
#: the PLAN files are the wave plans, BLOCKERS and the state-of-the-project page are dated.
EXCLUDED_PREFIXES = (
    "docs/plan/STATUS.md",
    "docs/plan/PLAN-",
    "docs/plan/BLOCKERS.md",
    "docs/plan/STATE-OF-THE-PROJECT-",
)
RENAMED_HEADING = "## Renamed in 0.21 (ADR 0098)"

#: Backticked tokens that name no file of a fresh checkout, each with its reason.
HISTORICAL: dict[str, str] = {
    "tools/call": "the MCP method `tools/call`, not a path",
    "tools/list": "the MCP method `tools/list`, not a path",
    "docker/dockerfile": "the BuildKit frontend reference `docker/dockerfile:1`, not a path",
    "packages/adapters/memory/pgvector.py": "docs/plan/04: a fallback adapter, never built",
    "charts/choregos/values/gateway.yaml": "docs/plan/04: a sketched base configuration, never split out",
}


def _pages() -> list[pathlib.Path]:
    pages = sorted(DOCS.glob("*.md"))
    pages += sorted((DOCS / "runbooks").glob("*.md"))
    pages += sorted((DOCS / "plan").glob("*.md"))
    return [p for p in pages if not p.relative_to(ROOT).as_posix().startswith(EXCLUDED_PREFIXES)]


def _path_tokens(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    in_renamed = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            in_renamed = line.startswith(RENAMED_HEADING)
        scanned = line
        if in_renamed and line.startswith("|"):
            # The first cell of a rename table is the former name: gone from the tree on purpose.
            scanned = "|".join(line.split("|")[2:])
        for match in BACKTICKED.finditer(scanned):
            token = match.group(1).strip()
            if not token.startswith(PREFIXES) or re.search(r"[\s*<{]", token):
                continue
            found.append((number, SUFFIX.sub("", token).rstrip(".,;)")))
    return found


def test_backticked_repository_paths_exist() -> None:
    missing: list[str] = []
    for page in _pages():
        for number, token in _path_tokens(page.read_text(encoding="utf-8")):
            if token in HISTORICAL or (ROOT / token).exists():
                continue
            missing.append(f"{page.relative_to(ROOT).as_posix()}:{number}: `{token}`")
    assert not missing, "backticked paths that are not in the tree:\n" + "\n".join(missing)


def test_historical_entries_are_still_needed() -> None:
    """An allowlist entry that now exists, or that no page names, is a leftover: delete it."""
    named = {token for page in _pages() for _, token in _path_tokens(page.read_text(encoding="utf-8"))}
    stale = [token for token in HISTORICAL if token not in named or (ROOT / token).exists()]
    assert not stale, f"HISTORICAL entries that are unused or exist: {stale}"
    assert all(HISTORICAL.values()), "every HISTORICAL entry carries a reason"


def test_no_upgrade_fragment_left() -> None:
    assert not (DOCS / "upgrade-notes").exists(), "docs/upgrade-notes is folded into docs/deployment.md"
    text = (DOCS / "deployment.md").read_text(encoding="utf-8")
    assert "## Renamed in 0.21 (ADR 0098)" in text
    renamed = text.split("## Renamed in 0.21 (ADR 0098)", 1)[1]
    # One row of each kind of fragment: operator names, extension API, services, log keys, trials.
    for name in (
        "CHOREGOS_ONTOLOGY_TRIAL",
        "`choregos_api.extensions`",
        "`choregos_api.edition`",
        "`choregos_api.services`",
        "`a2a.call` log key",
        "TRIAL_FAKES_URL",
        "CHOREGOS_RECORD_HISTORY",
        "`tools:`",
    ):
        assert name in renamed, f"the 0.21 rename section lost {name}"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_docs", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_docs_have_no_french() -> None:
    """The live scan of the docs area finds no French line (a stub carries `lang: fr-ok-file`)."""
    checker = _load_checker()
    findings = [
        finding
        for finding in checker.scan_tree(("docs/",))
        if checker.area_of(finding.path) in {"docs", "docs-plan"}
    ]
    assert not findings, "French left in the docs:\n" + "\n".join(
        f"{finding.path}:{finding.line}" for finding in findings
    )
