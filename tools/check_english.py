#!/usr/bin/env python3
"""Refuse French in new or modified lines (ADR 0046).

    uv run python tools/check_english.py                      # origin/main...HEAD
    uv run python tools/check_english.py --range A...B        # what CI runs on a pull request
    uv run python tools/check_english.py --no-commits         # lines only, not commit messages

Only lines a change ADDS are read: the existing French code is renamed package by package
(S34-04), and a guard that failed on every untouched line would be switched off on day one.
In those lines, only what is prose is read: comments, docstrings and string literals in Python
and TypeScript, JSX text, Markdown outside code blocks, YAML values and comments, Makefile
comments. Identifiers are keys, not prose: a modified line that calls an existing French
function is not flagged; a sentence that names one should put it in backticks.

The detector extends the one of `tests/langue/test_anglais.py` (ADR 0039): French-only accents,
a French lexicon, French contractions (`l'`, `d'`, `qu'`), French function words. A line that
must stay French (a Temporal patch id, a French alias kept for running workflows, a test sample
of French) carries the escape `lang: fr-ok` in a comment on the same line.
"""

from __future__ import annotations

import argparse
import io
import pathlib
import re
import shutil
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RANGE = "origin/main...HEAD"
ESCAPE = "lang: fr-ok"

#: Where prose lives. Root-level Markdown (AGENTS.md, README.md) and the Makefile are read too.
SCANNED_DIRS = ("packages/", "apps/", "tools/", "tests/", "docs/", ".github/", "charts/")
#: `.tpl` and `.txt`: a Helm chart's helpers and its NOTES, read line by line as YAML is.
SUFFIXES = {".py", ".ts", ".tsx", ".md", ".yaml", ".yml", ".tpl", ".txt"}
#: Paths never read, and why. A line here is a decision, not an oversight.
EXCLUDED = (
    "docs/fr/",  # the archived French documentation, no longer maintained
    "docs/cli.md",  # generated from the CLI's help strings, which are read at their source
    "packages/contracts/ts/",  # generated from the schemas, which are read at their source
    # The injection guard's patterns also recognise an injection written in French.
    "packages/core/src/choregos_core/injection.py",
)
EXCLUDED_NAMES = {"pnpm-lock.yaml"}

# ───────────────────────────────────────── the detector ─────────────────────────────────────────

#: Accents of French that English does not use; ü, ö, ä, ñ are left out (German, Spanish names).
_ACCENTS = re.compile(r"[àâçéèêëîïôûùœÀÂÇÉÈÊËÎÏÔÛÙŒ«»]")  # lang: fr-ok
#: Proper names and English loanwords keep their accents.
PROPER_NAMES = ("Léa", "Hélène", "Zoé", "Chloé", "Céline", "André", "Noël", "Québec", "Montréal")
_LOANWORDS = re.compile(
    r"\b(café|cafés|naïve|naïvely|naïveté|résumé|résumés|déjà|façade|façades|cliché|clichés|fiancé|"
    r"protégé|touché|née|exposé|entrée|entrées|crème|élan|vis-à-vis|à\s+la)\b",
    re.IGNORECASE,
)


def _lexicon(*lines: str) -> frozenset[str]:
    return frozenset(word for line in lines for word in line.split())


#: Words no English sentence uses: one is enough in a text of two words or more. The last line is
#: the lexicon of `tests/langue/test_anglais.py`, which caught `En cours` and `Intervention humaine`.
#: `comment`, `faire`, `sans` and `reprise` are English words too, and are left out.
STRONG = _lexicon(
    "dans avec leur leurs chaque sont une cette ces nous vous mais aussi",  # lang: fr-ok
    "alors donc puis rien encore lorsque parce puisque quand peut doit faut",  # lang: fr-ok
    "etre avoir sera sinon depuis selon toujours jamais ainsi voici celui",  # lang: fr-ok
    "celle ceux dont lequel laquelle quel quelle aucun aucune autre autres",  # lang: fr-ok
    "tous toutes fois chez ici cela ça quoi pourquoi doute peu lire",  # lang: fr-ok
    "cours ouvert ouverte humain humaine cadrage relecture besoin demande",  # lang: fr-ok
    "poste compte groupe commande ecris verifie",  # lang: fr-ok
)
#: French function words English text can contain once (`de facto`, `en route`, `et al.`):
#: two are needed, in lowercase or capitalised — `EST`, `DES`, `AU` are acronyms.
WEAK = _lexicon(
    "les des est pour par sur du au aux ou ne et le la un ce il elle que",  # lang: fr-ok
    "qui pas en de se sa ses tout sous vers ni lui",  # lang: fr-ok
)
#: `l'état`, `d'un`, `qu'il`, `n'est`, `c'est`; `D'Angelo` and `O'Brien` are names.
_CONTRACTION = re.compile(r"^(?:[ldnjcmst]|qu|L|C|J|N|S|Qu)['’][a-zà-ÿ]")
_EDGE_PUNCTUATION = "\"'`.,;:!?()[]{}«»…*_~<>|“”‘’-–—"
#: A token with one of these is code, a path, an URL or a number, not a word.
_NOT_A_WORD = re.compile(r"[/\\=@<>{}()\[\]#$%*|~^+0-9_]|\.\w|[a-z][A-Z]")
_CODE_SPAN = re.compile(r"`[^`]*`")
_QUOTED_KEY = re.compile(r"([\"'])[\w.\-/:]+\1")
_URL = re.compile(r"https?://\S+")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")


def _words(text: str) -> list[str]:
    text = _URL.sub(" ", _LINK_TARGET.sub("]", text))
    text = _QUOTED_KEY.sub(" ", _CODE_SPAN.sub(" ", text))
    words = []
    for raw in text.split():
        token = raw.strip(_EDGE_PUNCTUATION)
        if token and not _NOT_A_WORD.search(token):
            words.append(token)
    return words


def french_reason(text: str) -> str | None:
    """Why `text` reads as French, or None. Code spans, quoted keys, URLs and identifiers
    (anything with `_`, `/`, a digit, a dot or camelCase) are not read."""
    words = _words(text)
    if not words:
        return None
    prose = " ".join(words)
    for name in PROPER_NAMES:
        prose = prose.replace(name, "")
    prose = _LOANWORDS.sub("", prose)
    accent = _ACCENTS.search(prose)
    if accent:
        return f"French accent {accent.group()!r}"
    for word in words:
        if _CONTRACTION.match(word):
            return f"French contraction {word!r}"
    if len(words) < 2:
        return None  # a lone word is a key or a name, unless it carries an accent
    lowered = [w.lower() for piece in words for w in piece.split("-")]
    strong = [w for w in lowered if w in STRONG]
    if strong:
        return f"French word {strong[0]!r}"
    weak = [
        w for piece in words for w in piece.split("-") if w in WEAK or (w.istitle() and w.lower() in WEAK)
    ]
    if len(weak) >= 2:
        return f"French function words {' '.join(weak[:4])!r}"
    return None


def looks_french(text: str) -> bool:
    return french_reason(text) is not None


# ─────────────────────────────── what is prose, language by language ───────────────────────────────


_STRING_PREFIX = re.compile(r"^[rRbBuU]{1,2}(?=[\"'])")


def _python_prose(content: str) -> Iterator[tuple[int, str]]:
    """Comments, docstrings and strings, line by line; an identifier is never read."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        yield from enumerate(content.splitlines(), start=1)
        return
    for token in tokens:
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string.lstrip("#")
        elif token.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
            if "\\b" in token.string:
                continue  # a regular expression, not a sentence
            text = _STRING_PREFIX.sub("", token.string)
            for offset, line in enumerate(text.splitlines()):
                yield token.start[0] + offset, line


_TS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`",
    re.S,
)
#: JSX text: what sits between a tag's `>` and the next `<` or `{` — not an arrow's `=>`.
_JSX_TEXT = re.compile(r"(?<=[\w\"'}/])>(?!=)([^<>{}]+)")


def _ts_prose(content: str, *, jsx: bool) -> Iterator[tuple[int, str]]:
    position = 0
    for match in _TS_TOKEN.finditer(content):
        if jsx:
            yield from _jsx_text(content, position, match.start())
        line = content.count("\n", 0, match.start()) + 1
        for offset, piece in enumerate(match.group().splitlines()):
            yield line + offset, piece.strip("/*`'\" ")
        position = match.end()
    if jsx:
        yield from _jsx_text(content, position, len(content))


def _jsx_text(content: str, start: int, end: int) -> Iterator[tuple[int, str]]:
    for match in _JSX_TEXT.finditer(content, start, end):
        line = content.count("\n", 0, match.start()) + 1
        for offset, piece in enumerate(match.group(1).splitlines()):
            yield line + offset, piece


_FENCE = re.compile(r"^\s*(```|~~~)")


def _markdown_prose(content: str) -> Iterator[tuple[int, str]]:
    in_fence = False
    for number, line in enumerate(content.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, re.sub(r"<!--.*?-->", "", line)


_YAML_KEY = re.compile(r"^\s*(?:-\s+)?[\w.\-/\"']+\s*:(?=\s|$)")


def _yaml_prose(content: str) -> Iterator[tuple[int, str]]:
    """A key is an identifier; its value, a block scalar's lines and comments are read."""
    for number, line in enumerate(content.splitlines(), start=1):
        yield number, _YAML_KEY.sub("", line).lstrip(" -")


def _makefile_prose(content: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(content.splitlines(), start=1):
        if "#" in line:
            yield number, line.split("#", 1)[1].lstrip("#")


def prose_lines(path: str, content: str) -> Iterator[tuple[int, str]]:
    """(line number, prose) for every piece of prose in a file, by its kind."""
    suffix = pathlib.PurePosixPath(path).suffix
    if suffix == ".py":
        return _python_prose(content)
    if suffix in (".ts", ".tsx"):
        return _ts_prose(content, jsx=suffix == ".tsx")
    if suffix == ".md":
        return _markdown_prose(content)
    if suffix in (".yaml", ".yml", ".tpl", ".txt"):
        return _yaml_prose(content)
    return _makefile_prose(content)


def is_scanned(path: str) -> bool:
    pure = pathlib.PurePosixPath(path)
    if path.startswith(EXCLUDED) or pure.name in EXCLUDED_NAMES:
        return False
    if path == "Makefile":
        return True
    if pure.suffix not in SUFFIXES:
        return False
    return (len(pure.parts) == 1 and pure.suffix == ".md") or path.startswith(SCANNED_DIRS)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}: {self.text.strip()[:100]}"


def findings_in(path: str, content: str, added: set[int]) -> list[Finding]:
    """French in the prose of the `added` lines of a file whose new content is `content`."""
    lines = content.splitlines()
    found = []
    for number, prose in prose_lines(path, content):
        if number not in added or ESCAPE in lines[number - 1]:
            continue
        reason = french_reason(prose)
        if reason:
            found.append(Finding(path, number, reason, lines[number - 1]))
    return found


# ───────────────────────────────────────────── git ─────────────────────────────────────────────

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(diff: str) -> dict[str, set[int]]:
    """{path: numbers of the lines added in the new file} from a `--unified=0` diff."""
    result: dict[str, set[int]] = {}
    path: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path is not None and (hunk := _HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or "1")
            result.setdefault(path, set()).update(range(start, start + count))
    return result


GIT = shutil.which("git") or "git"


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - a fixed program; the arguments are refs and paths
        [GIT, *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _ends(diff_range: str) -> tuple[str, str]:
    """(base, head) of `A...B` (base = merge base) or `A..B`."""
    if "..." in diff_range:
        left, right = diff_range.split("...", 1)
        right = right or "HEAD"
        return _git("merge-base", left, right).strip(), right
    left, _, right = diff_range.partition("..")
    return left, right or "HEAD"


def scan_range(base: str, head: str) -> list[Finding]:
    # `-C`: a file renamed with a redirect left at its old name is a copy, not new lines (S34-05).
    diff = _git(
        "diff", "--unified=0", "--no-color", "--no-ext-diff", "-M", "-C", "--diff-filter=ACMR", base, head
    )
    found = []
    for path, added in sorted(added_lines(diff).items()):
        if is_scanned(path):
            found += findings_in(path, _git("show", f"{head}:{path}"), added)
    return found


_TRAILER = re.compile(r"^[A-Za-z-]+: .+<.+@.+>$")


def scan_commits(base: str, head: str) -> list[Finding]:
    """Commit messages of `base..head`, trailers (`Signed-off-by: …`) aside."""
    found = []
    for sha in _git("rev-list", "--no-merges", f"{base}..{head}").split():
        for number, line in enumerate(_git("show", "-s", "--format=%B", sha).splitlines(), start=1):
            if _TRAILER.match(line) or ESCAPE in line:
                continue
            reason = french_reason(line)
            if reason:
                found.append(Finding(f"commit {sha[:10]}", number, reason, line))
    return found


def _resolves(ref: str) -> bool:
    try:
        _git("rev-parse", "--verify", "--quiet", ref)
    except subprocess.CalledProcessError:
        return False
    return True


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument("--range", dest="diff_range", help=f"git range to read (default {DEFAULT_RANGE})")
    parser.add_argument("--no-commits", action="store_true", help="do not read commit messages")
    args = parser.parse_args(list(argv) if argv is not None else None)

    diff_range = args.diff_range or DEFAULT_RANGE
    left = re.split(r"\.\.\.?", diff_range, maxsplit=1)[0]
    if args.diff_range is None and not _resolves(left):
        # A clone without the remote (an archive, a fork's first checkout): nothing to compare to.
        # CI always passes `--range`, and there an unknown ref is an error.
        print(f"check_english: {left} does not resolve; nothing to compare to, skipped")
        return 0
    base, head = _ends(diff_range)
    found = scan_range(base, head)
    if not args.no_commits:
        found += scan_commits(base, head)
    for finding in found:
        print(finding)
    if found:
        print(
            f"\n{len(found)} line(s) in French (ADR 0046: everything new is in English). Name an "
            f"identifier in backticks; a line that must stay French carries `{ESCAPE}` in a comment.",
            file=sys.stderr,
        )
        return 1
    print(f"check_english: no French in the lines added by {diff_range}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
