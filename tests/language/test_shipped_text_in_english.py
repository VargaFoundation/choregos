"""Everything a user reads is in English (ADR 0039); the code and its comments are English too.

The detector is deliberately simple (accented letters, a word no English label uses, or French
function words) and its false positives are settled by a short list of proper names, never by path
exceptions. Since ADR 0098 it lives in `tools/check_english.py` (`looks_like_shipped_french`); this
module loads that tool by its path (under `--import-mode=importlib` a test module does not import
another one) and keeps the former name as an alias for the guards below.

What the platform SHIPS is read in English.

The 2026-10-07 review saw French labels in an English console and it
came from the shipped templates, not from the screens. This test walks what a user reads in what
is shipped: state labels, descriptions, field and task titles, attestations, agent instructions
and descriptions, skills, and the files dropped into a project's repository. The identifiers
(names of states, fields, objects) stay what they are: they are keys, not text.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _check_english() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


_CHECK_ENGLISH = _check_english()
looks_french = _CHECK_ENGLISH.looks_like_shipped_french
_prose: Callable[[str], str] = _CHECK_ENGLISH.shipped_prose
#: The keys whose value is shown to someone.
VISIBLE_KEYS = {
    "display",
    "displayName",
    "display_name",
    "title",
    "description",
    "instructions",
    "attest",
    "justification",
    "motif",  # lang: fr-ok
    "message",
    "summary",
}
SHIPPED_YAML = [
    *sorted((ROOT / "packages/core/src/choregos_core/dsl/templates").glob("*.yaml")),
    *sorted((ROOT / "packages/core/src/choregos_core/presets").glob("*.yaml")),
    *sorted((ROOT / "templates").rglob("*.yaml")),
    *sorted((ROOT / "demo/workflows").glob("*.yaml")),
    *sorted((ROOT / "packages/core/src/choregos_core/agent_catalogue").rglob("*.yaml")),
]
SHIPPED_TEXTS = sorted(
    path
    for folder in ("templates", "packages/core/src/choregos_core/agent_catalogue")
    for path in (ROOT / folder).rglob("*")
    if path.is_file() and path.suffix in {".md", ".j2"}
)


def _visible_texts(value: Any, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        if key.rsplit(".", 1)[-1] in VISIBLE_KEYS:
            yield key, value
    elif isinstance(value, dict):
        for sub_key, sub_value in value.items():
            yield from _visible_texts(sub_value, f"{key}.{sub_key}" if key else str(sub_key))
    elif isinstance(value, list):
        for element in value:
            yield from _visible_texts(element, key)


def test_the_detector_recognises_french_and_lets_english_through() -> None:
    assert looks_french("Intervention humaine")  # lang: fr-ok
    assert looks_french("En cours")  # lang: fr-ok
    assert looks_french("Remettre le badge en main propre")  # lang: fr-ok
    assert not looks_french("Needs a human")
    assert not looks_french("Hand the badge to its holder in person")
    assert not looks_french("Léa's Claude Code")


@pytest.mark.parametrize("path", SHIPPED_YAML, ids=lambda c: str(c.relative_to(ROOT)))
def test_no_shipped_text_is_in_french(path: pathlib.Path) -> None:
    faults = [
        f"{key}: {text[:80]!r}"
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        for key, text in _visible_texts(document)
        if looks_french(_prose(text))
    ]
    assert not faults, "French in what the platform ships:\n  " + "\n  ".join(faults)


@pytest.mark.parametrize("path", SHIPPED_TEXTS, ids=lambda c: str(c.relative_to(ROOT)))
def test_no_dropped_file_nor_skill_is_in_french(path: pathlib.Path) -> None:
    prose = _prose(path.read_text(encoding="utf-8"))
    faults = [line.strip()[:80] for line in prose.splitlines() if looks_french(line)]
    assert not faults, "French in a shipped file:\n  " + "\n  ".join(faults)


# ───────────────────── what the code SAYS: the messages (S21-10 onwards) ─────────────────────


def test_the_core_messages_are_in_english() -> None:
    """Validator, parser, map, guarantees, engine, typed editions, policy, secrets (S21-10)."""
    faults = _CHECK_ENGLISH.french_spoken_strings("packages/core/src/choregos_core")
    assert not faults, "French in what the core says:\n  " + "\n  ".join(faults)


# ──────────────── the contracts: the OpenAPI reference and the JSON Schemas (S21-11) ────────────────

CONTRACTS = ROOT / "packages/contracts"


def test_the_contracts_are_in_english() -> None:
    """The OpenAPI reference (summaries, descriptions, responses, parameters, schemas), the
    descriptions of the JSON Schemas (which the API serves and which the editor and the generated
    types show) and the messages the contracts raise. The identifiers (paths, operationId,
    properties, enum) are not read there: they are keys."""
    documents = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((CONTRACTS / "schemas").glob("*.json"))
    ]
    openapi = CONTRACTS / "openapi.yaml"
    documents.append((openapi, yaml.safe_load(openapi.read_text(encoding="utf-8"))))
    faults = [
        f"{path.relative_to(ROOT)}: {key}: {text[:80]!r}"
        for path, document in documents
        for key, text in _visible_texts(document)
        if looks_french(_prose(text))
    ]
    faults += _CHECK_ENGLISH.french_spoken_strings("packages/contracts/src/choregos_contracts")
    assert not faults, "French in the contracts:\n  " + "\n  ".join(faults)
