"""Every relative link of the documentation resolves (S34-05).

The ADR and plan files were renamed in English; the old names stay as one-line redirects, so a link
written before the rename, or in a branch still in flight, keeps resolving. This test is what makes
"keeps resolving" true: a link to a file that does not exist — a typo, a rename without its
redirect, a redirect pointing nowhere — fails here. The French archives (`docs/fr`, the French-named ADR
stubs and plan pages) were deleted in 0.21 (ADR 0098); the tests below keep them deleted.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: `[text](target)` — the target up to a space, a `#` or the closing parenthesis.
LINK = re.compile(r"\]\(\s*<?([^)\s#>]+)")
#: Fenced code blocks hold examples, not links.
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
#: `docs/site/` is checked by `test_site.py`, which also reads headings and tolerates, for the sections
#: S62-09b writes, a link to a page that has not landed yet.
SITE = ROOT / "docs" / "site"
PAGES = sorted(
    p
    for p in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / "templates").rglob("*.md")]
    if not p.is_relative_to(SITE)
)


def _targets(page: pathlib.Path) -> list[str]:
    text = FENCE.sub("", page.read_text("utf-8"))
    return [
        t
        for t in LINK.findall(text)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", t) and not t.startswith(("/", "{", "$"))
    ]


def test_there_are_pages_to_check() -> None:
    assert len(PAGES) > 50, "the link check found no documentation: it checks nothing"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_resolves(page: pathlib.Path) -> None:
    broken = [t for t in _targets(page) if not (page.parent / t).exists()]
    assert not broken, f"{page.relative_to(ROOT)} links to files that do not exist: {broken}"


def test_a_redirect_points_to_a_page_that_exists() -> None:
    redirects = [p for p in PAGES if p.read_text("utf-8").startswith("# Moved\n")]
    for page in redirects:
        assert _targets(page), f"{page.name} is a redirect with no target"


def test_there_is_no_french_archive() -> None:
    assert not (ROOT / "docs" / "fr").exists(), "docs/fr was deleted (ADR 0098); do not bring it back"


def test_no_french_named_page_in_docs() -> None:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_docs", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    check_english = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = check_english
    spec.loader.exec_module(check_english)
    named = []
    for page in (ROOT / "docs").rglob("*.md"):
        rel = page.relative_to(ROOT).as_posix()
        if check_english.has_file_escape(page.read_text("utf-8")):
            continue
        if check_english.path_reason(rel) is not None:
            named.append(rel)
    assert not named, f"pages with a French name under docs/: {sorted(named)}"
