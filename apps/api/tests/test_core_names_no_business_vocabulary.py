"""The static half of the genericity guard (S63-19, ADR 0133 point 7): the core names no business.

The fixture plugin `investment-assistant` must run with no code of its own in the core, so no identifier of
the core (`packages/*/src`, `apps/api/src`, `apps/orchestrator/src`) may be built from its vocabulary
(`investment`, `isin`, `instrument`, `market_data`, `brokerage`...; `quote` is left out: `urllib.parse.quote`
is everywhere). Identifiers are Python names
(snake_case and CamelCase are split into words, so `isinstance` is not `isin`). Comments, docstrings and
string literals are read too (the same words, whole, plural allowed; `market data` with a space, a hyphen
or an underscore): the core's messages and prompts must not name the business either.

To see it fail, add a business word to any identifier of the core, for instance `def isin_of(...)` in
`apps/api/src/choregos_api/services/assistants.py`: the test names the file, the line and the word. To
allow a word that is legitimately there (a connector of S38, say), add the file to `ALLOWED` with the reason:
the allowlist is checked both ways, so an entry that no longer matches anything fails too.
"""

from __future__ import annotations

import io
import itertools
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ("packages/*/src", "apps/api/src", "apps/orchestrator/src")
VOCABULARY = {
    "investment",
    "investor",
    "isin",
    "instrument",
    "brokerage",
    "portfolio",
    "marketdata",
    "securities",
}
#: file -> reason. Each entry names a word the file legitimately carries.
ALLOWED: dict[str, str] = {
    "packages/adapters/src/choregos_adapters/marketdata": (
        "S38's market-data connectors (Tiingo, composite) are a product feature, not of assistants"
    ),
    "packages/adapters/src/choregos_adapters/fakes/marketdata.py": "the fake of the S38 market-data provider",
    "packages/adapters/src/choregos_adapters/fakes/scenes.py": "the S38 scene that builds the fake provider",
    "packages/adapters/src/choregos_adapters/families.py": "the operations of the S38 `marketdata` family",
    "packages/adapters/src/choregos_adapters/__init__.py": "exports the S38 fake `FAKE_MARKETDATA`",
    "packages/adapters/src/choregos_adapters/specs.py": "registers the S38 `marketdata` connector type",
    "packages/adapters/src/choregos_adapters/builtins.py": "registers the S38 `marketdata` connector type",
    "packages/contracts/src/choregos_contracts/workflow.py": (
        "a comment names the S38 `marketdata` connector family a command may call"
    ),
    "packages/quant/src/choregos_quant": "S38's research tooling reads market data through the S38 connector",
}


def _words(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return [w for w in spaced.lower().split("_") if w]


def _business_words(name: str) -> set[str]:
    words = _words(name)
    found = {w for w in words if w in VOCABULARY}
    found |= {"market_data" for a, b in itertools.pairwise(words) if (a, b) == ("market", "data")}
    return found


def sources() -> list[Path]:
    return sorted(p for pattern in CORE for root in ROOT.glob(pattern) for p in root.rglob("*.py"))


#: Tokens that carry prose: comments, string literals, the literal parts of f-strings.
_TEXT_TOKENS = {tokenize.COMMENT, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
_WORD = re.compile(r"[A-Za-z]+(?:_[A-Za-z]+)*")
_MARKET_DATA = re.compile(r"market[ _-]data", re.IGNORECASE)


def _text_words(text: str) -> set[str]:
    found: set[str] = set()
    for raw in _WORD.findall(text):
        found |= _business_words(raw)
        if raw.lower().endswith("s"):
            found |= _business_words(raw[:-1])
    if _MARKET_DATA.search(text):
        found.add("market_data")
    return found


def hits_in(text: str) -> list[tuple[int, str, str]]:
    """(line, text, word) for every NAME token (the identifier), comment and string of a module."""
    found: list[tuple[int, str, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.NAME:
            found += [(token.start[0], token.string, word) for word in sorted(_business_words(token.string))]
        elif token.type in _TEXT_TOKENS:
            found += [(token.start[0], token.string[:40], word) for word in sorted(_text_words(token.string))]
    return found


def _allowed(relative: str) -> str | None:
    return next((reason for prefix, reason in ALLOWED.items() if relative.startswith(prefix)), None)


def test_no_identifier_of_the_core_names_the_business_vocabulary() -> None:
    files = sources()
    assert len(files) > 500, "the core's sources were found"
    offences: list[str] = []
    used: set[str] = set()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        hits = hits_in(path.read_text(encoding="utf-8"))
        if not hits:
            continue
        if _allowed(relative):
            used.update(prefix for prefix in ALLOWED if relative.startswith(prefix))
            continue
        offences += [f"{relative}:{line}: `{name}` names `{word}`" for line, name, word in hits]
    assert not offences, "the core must stay generic (ADR 0133 point 7):\n" + "\n".join(offences)
    assert set(ALLOWED) == used, f"allowlist entries that match nothing: {sorted(set(ALLOWED) - used)}"


def test_the_guard_sees_a_business_word_in_an_identifier() -> None:
    source = "def price_of(isin):\n    market_data = Instrument()\n    return isinstance(x, y)\n"
    assert [(line, word) for line, _, word in hits_in(source)] == [
        (1, "isin"),
        (2, "market_data"),
        (2, "instrument"),
    ]
    assert hits_in("ok = isinstance(a, b)\nx = 'a quote'\n") == []


def test_the_guard_sees_a_business_word_in_a_comment_a_docstring_or_a_string() -> None:
    source = (
        '"""Docs on the portfolio."""\n# an instrument\nx = "ISIN codes"\n'
        'y = f"market data {x}"\nz = "instruments"\n'
    )
    assert [(line, word) for line, _, word in hits_in(source)] == [
        (1, "portfolio"),
        (2, "instrument"),
        (3, "isin"),
        (4, "market_data"),
        (5, "instrument"),
    ]
