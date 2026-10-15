"""The identifiers of a shipped template are English (ADR 0039, amended by S39-11).

ADR 0039 left identifiers alone ("keys, not text"). They are visible all the same: a state id is on
the map and in the YAML editor, a connector name in `/admin/connectors` and in the tool the agent
calls, an agent slug in the URL `/agents/<slug>`, an action kind in `/actions`. The prose of a
template is read by `tools/check_english.py` (the `templates/` directory is scanned); here, every
KEY and every VALUE of every YAML document of every template, and every path, is split into words
and each word is looked up in the lexicon of French identifier words.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ce = _load()


def _strings(node: Any) -> Iterator[str]:
    """Every key and every string value of a parsed document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def french_words_in(path: pathlib.Path, text: str) -> list[tuple[str, str]]:
    """(where, French word) for each French identifier word of one template file."""
    found = [(str(path), word) for word in ce.french_identifier_words(str(path))]
    if path.suffix in {".yaml", ".yml"}:
        for document in yaml.safe_load_all(text):
            for string in _strings(document):
                found += [(string, word) for word in ce.french_identifier_words(string)]
    elif path.name == "SKILL.md" and text.startswith("---"):
        header = yaml.safe_load(text.split("---", 2)[1]) or {}
        found += [(str(v), w) for v in header.values() for w in ce.french_identifier_words(str(v))]
    return found


def _template_files() -> list[pathlib.Path]:
    return sorted(p for p in TEMPLATES.rglob("*") if p.is_file() and p.suffix in {".yaml", ".yml", ".md"})


def test_there_are_templates_to_read() -> None:
    assert len({p.relative_to(TEMPLATES).parts[0] for p in _template_files()}) >= 4


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: str(p.relative_to(TEMPLATES)))
def test_every_identifier_of_a_template_is_english(path: pathlib.Path) -> None:
    relative = path.relative_to(TEMPLATES)
    found = french_words_in(relative, path.read_text(encoding="utf-8"))
    assert not found, f"{relative}: French identifier words {found[:6]}"


def test_a_planted_french_state_id_is_caught() -> None:
    planted = "states:\n  demande: { display: Request received }\n  a_revoir: { display: To review }\n"
    words = {word for _, word in french_words_in(pathlib.Path("w/x.yaml"), planted)}
    assert {"demande", "revoir"} <= words


@pytest.mark.parametrize(
    "identifier",
    ["t-coupure", "depart.coupure", "date_arrivee", "consigner_le_badge", "agent:coordinateur-onboarding"],
)
def test_a_french_identifier_is_caught_by_part(identifier: str) -> None:
    assert ce.french_identifier_words(identifier), identifier


@pytest.mark.parametrize(
    "identifier",
    ["start_date", "t-approve", "offboarding.cut_access", "badge-readers", "requested", "laptop_condition"],
)
def test_an_english_identifier_goes_through(identifier: str) -> None:
    assert ce.french_identifier_words(identifier) == []


def test_the_lexicon_has_no_english_word_in_it() -> None:
    """A word English also uses would flag a good identifier: the lexicon stays a net for French."""
    english = {
        "plan",
        "present",
        "service",
        "contract",
        "group",
        "date",
        "badge",
        "action",
        "state",
        "account",
    }
    assert not english & set(ce.IDENTIFIER_WORDS)
    assert re.fullmatch(r"[a-z]+", "".join(sorted(ce.IDENTIFIER_WORDS)))
