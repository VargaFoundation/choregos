"""Every relative link of the documentation resolves (S34-05).

The ADR and plan files were renamed in English; the old names stay as one-line redirects, so a link
written before the rename, or in a branch still in flight, keeps resolving. This test is what makes
"keeps resolving" true: a link to a file that does not exist — a typo, a rename without its
redirect, a redirect pointing nowhere — fails here. `docs/fr/` is an archive, no longer maintained:
it is not checked.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: `[text](target)` — the target up to a space, a `#` or the closing parenthesis.
LINK = re.compile(r"\]\(\s*<?([^)\s#>]+)")
#: Fenced code blocks hold examples, not links.
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
PAGES = sorted(
    p
    for p in [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / "templates").rglob("*.md")]
    if "docs/fr/" not in p.as_posix()
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
    assert len(redirects) >= 50, "the renamed ADR and plan pages lost their redirects"
    for page in redirects:
        assert _targets(page), f"{page.name} is a redirect with no target"
