"""The API's errors read in English (ADR 0039, S21-12).

What an HTTP client receives when the API refuses (the `title` and `detail` of an RFC 9457
problem, the `msg` of its `errors` list, the MCP door's refusal) is swept in the syntax tree of
`choregos_api` and of the routes the ontology mounts there: every string literal, and the constant
part of every f-string, passed to an error constructor (`not_found`, `forbidden`, `ApiError`...,
and the refusals the API returns as a response: `SkillRefused`, `ArgumentsRefused`, `Refusal` of
the door...) or stored under a `title`, `detail`, `msg` or `error` key of a literal dictionary. A
code excerpt (between backticks) is not prose: a name of a state, a field or a scope stays what it
is there.

The logs (`logger.info(...)`) are not read: they speak to operators, on the code side.

The detector is `looks_like_shipped_french` of `tools/check_english.py` (ADR 0098), loaded by its
path: under `--import-mode=importlib` a test module does not import another one, and a second copy
would drift. It did not know the vocabulary of the API's refusals: a French refusal with no accent
and no function word passed. The lexicon below completes it, for this sweep.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import sys
from collections.abc import Callable, Iterator
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: The API, and the ontology routes it serves (`/projects/{id}/ontology`, `/proposals`...).
SOURCES = (ROOT / "apps/api/src/choregos_api", ROOT / "packages/ontology/src/choregos_ontology/service")


def _language_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools/check_english.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


_GUARD = _language_guard()
looks_french = _GUARD.looks_like_shipped_french
_prose: Callable[[str], str] = _GUARD.shipped_prose

#: The words of the API's refusals that the shared detector let through, and the elision
#: (`l'agent`, `n'a`, `d'agent`) that no English contraction writes after a one-letter word.
_ERROR_LEXICON = re.compile(  # lang: fr-ok (the words to catch are French)
    r"\b(introuvable|existe|inconnue?s?|invalide|manquante?|manque|jetons?|aucune?|"  # lang: fr-ok
    r"plafond|atteint|fichiers?|gabarits?|exige|requise?|interdite?|projets?|"  # lang: fr-ok
    r"connecteurs?|outils?|appartenance|droits|traitable|authentification|erreur|"  # lang: fr-ok
    r"manifeste|illisible|orphelin|ambigu|resserrer|seule?|doit|peut|injoignable|"  # lang: fr-ok
    r"dossier|rejet|pourquoi|fourni|toute|dans|sur|de|du|trop|octets|mio|kio)\b"  # lang: fr-ok
    r"|\b(qu|[cdjlmnst])'\w",
    re.IGNORECASE,
)


#: The error constructors, and the refusals the API returns as they are to its client.
CONSTRUCTORS = {
    "not_found",
    "forbidden",
    "unauthorized",
    "conflict",
    "unprocessable",
    "upstream",
    "ApiError",
    "HTTPException",
    # The MCP door: HTTP responses and JSON-RPC errors.
    "Refusal",
    "TokenRefused",
    "ToolRefused",
    "_http_refusal",
    "_fault",
    "erreur",  # lang: fr-ok (the A2A door's JSON-RPC error builder keeps its name)
    # The refusals converted to 400 / 422 / 401 / 403 / 409 by a route.
    "SkillRefused",
    "ArgumentsRefused",
    "GestureRefused",
    "ToolRefusal",
    # The refusal returned to the run, whose reason the console shows.
    "AgentUnavailable",
}
#: The keys of a literal dictionary whose value goes into an error response.
READ_KEYS = {"title", "detail", "msg", "error"}

#: What stays French in an API error, and why: `(file, excerpt)`. This list can only get shorter:
#: `test_the_leftovers_list_keeps_only_what_exists` refuses an entry that no longer matches anything.
LEFT_IN_FRENCH: set[tuple[str, str]] = set()


def _name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _strings(node: ast.AST) -> Iterator[tuple[int, str]]:
    """The strings under a node; an f-string is read without its holes, in one piece."""
    in_an_fstring: set[int] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.JoinedStr):
            in_an_fstring.update(id(v) for v in inner.values)
            yield (
                inner.lineno,
                "".join(
                    v.value for v in inner.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ),
            )
    for inner in ast.walk(node):
        if (
            isinstance(inner, ast.Constant)
            and isinstance(inner.value, str)
            and id(inner) not in in_an_fstring
        ):
            yield inner.lineno, inner.value


def error_strings(source: str) -> Iterator[tuple[int, str]]:
    """What an API module can put into an error response."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _name(node) in CONSTRUCTORS:
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                yield from _strings(argument)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in READ_KEYS:
                    yield from _strings(value)


def french(text: str) -> bool:
    prose = _prose(text)
    return looks_french(prose) or bool(_ERROR_LEXICON.search(prose))


def _modules() -> Iterator[tuple[str, str]]:
    for path in sorted(c for folder in SOURCES for c in folder.rglob("*.py")):
        yield str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")


def _in_french() -> list[tuple[str, int, str]]:
    return [
        (relative, line, text)
        for relative, source in _modules()
        for line, text in sorted(set(error_strings(source)))
        if french(text)
    ]


def test_the_detector_recognises_the_apis_refusals() -> None:
    """The refusals from before S21-12 that no accent or function word gave away, and their translation."""
    for before in (
        "Projet introuvable",  # lang: fr-ok
        "jeton de run manquant",  # lang: fr-ok
        "signature HMAC invalide",  # lang: fr-ok
        "manifeste de template invalide",  # lang: fr-ok
        "`token_id` manque",  # lang: fr-ok
        "Plafond de findings atteint",  # lang: fr-ok
        "authentification requise",  # lang: fr-ok
    ):
        assert french(before), before
    for after in (
        "Project not found",
        "Project `acme/billing` does not exist",
        "missing run token",
        "invalid HMAC signature",
        "insufficient rights: `workflow:write` needs a higher role on acme/billing",
        "the secret must be a reference (`env:NAME`), not a value in clear",
        "Organisation `acme` does not exist",
        "the task asks you to attest: “the badge was handed over”",
    ):
        assert not french(after), after


def test_the_sweep_sees_the_apis_errors() -> None:
    """A sweep that no longer saw anything would be green for nothing: it sees the constructors."""
    seen = {text for _, source in _modules() for _, text in error_strings(source)}
    assert {"Forbidden", "Not authenticated", "State conflict", "Unprocessable entity"} <= seen
    assert " not found" in seen and "invalid shared secret" in seen
    assert len(seen) > 200


def test_no_api_error_is_in_french() -> None:
    faults = [
        f"{relative}:{line}: {text[:90]!r}"
        for relative, line, text in _in_french()
        if not any(relative == file and excerpt in text for file, excerpt in LEFT_IN_FRENCH)
    ]
    assert not faults, "French in what the API answers:\n  " + "\n  ".join(faults)


def test_the_leftovers_list_keeps_only_what_exists() -> None:
    """An entry translated since leaves the list: it can only get shorter."""
    remaining = _in_french()
    obsolete = [
        f"{file}: {excerpt!r}"
        for file, excerpt in sorted(LEFT_IN_FRENCH)
        if not any(relative == file and excerpt in text for relative, _, text in remaining)
    ]
    assert not obsolete, "already translated, to remove from LEFT_IN_FRENCH:\n  " + "\n  ".join(obsolete)
