"""The page that sets out the vocabulary of ADR 0181 exists, is reached, and names seams that exist (S83-01).

`docs/site/build/plugins-and-trades.md` tells a reader the core, a plugin (its content and its extension),
an extension seam and an edition apart. ADR 0181's decision of 2026-11-19 made "plugin" the one unit a
trade ships and dropped "pack". This test holds the page to that: it exists, the pages that introduce
plugins link to it, the seams and the entry-point group it names are the code's, and "pack" appears only
as the word it refuses.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "site" / "build" / "plugins-and-trades.md"

#: The pages that introduce plugins, extensions or editions: each sends its reader to the page.
LINKED_FROM = (
    "docs/README.md",
    "docs/concepts.md",
    "docs/development.md",
    "docs/plugins.md",
    "docs/site/build/plugins.md",
    "docs/site/concepts.md",
    "docs/site/reference/glossary.md",
)

#: The extension seams the page names, as `module.attribute` -> the module that holds them.
SEAMS = {
    "tranibor_adapters.register": "tranibor_adapters",
    "effects.declare_effect": "tranibor_api.effects",
    "extensions.declare_router": "tranibor_api.extensions",
    "edition.declare": "tranibor_api.edition",
}

_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _text() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_page_exists_with_its_title() -> None:
    assert PAGE.is_file(), "docs/site/build/plugins-and-trades.md is the vocabulary of ADR 0181 (S83-01)"
    assert "title: Plugins, extensions and editions" in _text()


def test_every_page_that_introduces_plugins_links_to_it() -> None:
    missing: list[str] = []
    for relative in LINKED_FROM:
        page = ROOT / relative
        targets = {(page.parent / link).resolve() for link in _LINK.findall(page.read_text(encoding="utf-8"))}
        if PAGE.resolve() not in targets:
            missing.append(relative)
    assert not missing, f"pages that no longer link to plugins-and-trades.md: {missing}"


def test_the_seams_it_names_are_the_code_s() -> None:
    text = _text()
    for seam, module in SEAMS.items():
        assert f"`{seam}`" in text, f"the page no longer names `{seam}`: update SEAMS with it"
        attribute = seam.rsplit(".", 1)[1]
        found = getattr(importlib.import_module(module), attribute, None)
        assert callable(found), f"`{seam}` is not in {module}"
    from tranibor_adapters import EXTENSION_GROUP

    assert f"`{EXTENSION_GROUP}`" in text, "the page names the entry-point group extensions load from"


def test_pack_is_only_the_word_it_refuses() -> None:
    bare = [m.group(0) for m in re.finditer(r'(?<!")\bpacks?\b(?!")', _text(), re.IGNORECASE)]
    assert not bare, f'"pack" names no unit any more (ADR 0181, 2026-11-19): {bare}'
