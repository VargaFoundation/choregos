# SPDX-License-Identifier: Apache-2.0
"""The user documentation the image carries, searched and read through the MCP door (S62-10, ADR 0150).

The API image copies `docs/site/**` (the Markdown of the public site) to `/opt/choregos/docs`
(`CHOREGOS_DOCS_DIR` overrides). The first `docs_search` or `docs_read` walks that directory once and
keeps an index in memory: one page per Markdown file, under the slug the public site gives it
(`<CHOREGOS_DOCS_URL>/<slug>`, the publishing identity's documentation by default).

A slug is a key of that index, never a path: `docs_read` cannot open anything the walk did not list,
and the walk lists only regular Markdown files under the directory, never through a symbolic link.
The ranking is BM25 over each page's body and headings, plus a boost for a match in its title: no
dependency, the same answer every time.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from choregos_core.publishing import PUBLISHING

#: Where the image puts the documentation (`docker/api.Dockerfile`).
DEFAULT_DOCS_DIR = "/opt/choregos/docs"
DEFAULT_DOCS_URL = PUBLISHING.documentation
#: A page larger than this is not documentation: it is left out of the index.
MAX_PAGE_BYTES = 512 * 1024
#: Bounds the walk: a directory mounted by mistake does not fill the API's memory.
MAX_PAGES = 2_000
#: What one `docs_read` returns at most; the rest is read with `offset`.
READ_CHUNK = 20_000
SNIPPET_SIZE = 220
#: The weight of a match in a heading against one in the body, and the boost of one in the title.
HEADING_WEIGHT = 2
TITLE_BOOST = 3.0
K1 = 1.2
B = 0.75

_EXTENSIONS = (".md", ".mdx")
_NUMBER_PREFIX = re.compile(r"^\d+\s*[-_.]+\s*(?=[^-_.\s])")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_CUSTOM_ID = re.compile(r"\s*\{#([\w-]+)\}\s*$")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_WORD = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how"),
        *("i", "in", "is", "it", "of", "on", "or", "the", "this", "to", "what", "when", "where"),
        *("which", "who", "why", "with", "you", "your"),
    }
)


class DocsError(Exception):
    """A request the documentation cannot answer: an unknown page or section, an empty query."""


@dataclass(frozen=True, slots=True)
class Section:
    anchor: str
    title: str
    level: int
    #: Offsets of the section in the page's Markdown: from its heading line to the next heading of the
    #: same or a higher level.
    start: int
    end: int


@dataclass(slots=True)
class Page:
    slug: str
    title: str
    #: The file's Markdown, front matter stripped, exactly as written otherwise.
    markdown: str
    is_index: bool
    sections: list[Section] = field(default_factory=list)
    body_terms: Counter[str] = field(default_factory=Counter)
    title_terms: frozenset[str] = frozenset()
    length: int = 0

    def url(self, base: str) -> str:
        base = base.rstrip("/")
        if not self.slug:
            return f"{base}/"
        return f"{base}/{self.slug}" + ("/" if self.is_index else "")


@dataclass(frozen=True, slots=True)
class Hit:
    page: Page
    score: float
    snippet: str


def terms(text: str) -> list[str]:
    """Lower-cased words without stop words, plurals folded: `Workflows` and `workflow` match."""
    found = []
    for word in _WORD.findall(text.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("ies"):
            found.append(word[:-3] + "y")
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            found.append(word[:-1])
        else:
            found.append(word)
    return found


def anchor_of(text: str) -> str:
    """The anchor Docusaurus gives a heading (github-slugger): lower case, punctuation dropped."""
    plain = _LINK.sub(r"\1", text).replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", plain.strip().lower()).replace(" ", "-")


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end == -1:
        if text.endswith("\n---"):
            end = len(text) - 4
        else:
            return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, text[end + len("\n---\n") :].lstrip("\n")


def _sections(markdown: str) -> list[Section]:
    headings: list[tuple[int, int, str, str]] = []  # offset, level, title, anchor
    seen: Counter[str] = Counter()
    offset = 0
    fenced = False
    for line in markdown.splitlines(keepends=True):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (match := _HEADING.match(line.rstrip("\n"))):
            title = match.group(2)
            custom = _CUSTOM_ID.search(title)
            if custom:
                anchor, title = custom.group(1), title[: custom.start()]
            else:
                anchor = anchor_of(title)
                if seen[anchor]:
                    anchor = f"{anchor}-{seen[anchor]}"
            seen[anchor_of(title) if not custom else anchor] += 1
            headings.append((offset, len(match.group(1)), _LINK.sub(r"\1", title).strip(), anchor))
        offset += len(line)
    sections = []
    for position, (start, level, title, anchor) in enumerate(headings):
        end = len(markdown)
        for later, later_level, _, _ in headings[position + 1 :]:
            if later_level <= level:
                end = later
                break
        sections.append(Section(anchor, title, level, start, end))
    return sections


def slug_of(relative: Path) -> tuple[str, bool]:
    """The public slug of a file under `docs/site/`, and whether it is a folder's index page.

    Docusaurus strips number prefixes (`01-concepts/` → `concepts/`) and serves `index.md`,
    `README.md` and a file named like its folder at the folder's URL."""
    parts = [_NUMBER_PREFIX.sub("", part) for part in relative.parts]
    stem = parts[-1].rsplit(".", 1)[0]
    folders = parts[:-1]
    if stem.lower() in {"index", "readme"} or (folders and stem == folders[-1]):
        return "/".join(folders), True
    return "/".join([*folders, stem]), False


def _page(path: Path, relative: Path) -> Page | None:
    try:
        if path.stat().st_size > MAX_PAGE_BYTES:
            return None
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    meta, markdown = _split_front_matter(text)
    slug, is_index = slug_of(relative)
    sections = _sections(markdown)
    title = meta.get("title") or next((s.title for s in sections if s.level == 1), "")
    title = title or (slug.rsplit("/", 1)[-1].replace("-", " ").capitalize() if slug else "Overview")
    heading_terms = Counter(terms(" ".join(s.title for s in sections)))
    body_terms = Counter(terms(markdown))
    for term, count in heading_terms.items():
        body_terms[term] += (HEADING_WEIGHT - 1) * count  # a heading's words already count once in the body
    return Page(
        slug=slug,
        title=title,
        markdown=markdown,
        is_index=is_index,
        sections=sections,
        body_terms=body_terms,
        title_terms=frozenset(terms(f"{title} {slug.replace('/', ' ')}")),
        length=sum(body_terms.values()),
    )


class Index:
    """Every page of the bundled documentation, by slug, with what ranks them."""

    def __init__(self, pages: list[Page]) -> None:
        self.pages: dict[str, Page] = {}
        for page in sorted(pages, key=lambda p: p.slug):
            self.pages.setdefault(page.slug, page)  # `concepts.md` and `concepts/index.md`: the first wins
        documents = list(self.pages.values())
        self.average = sum(p.length for p in documents) / len(documents) if documents else 0.0
        frequency: Counter[str] = Counter()
        for page in documents:
            frequency.update(set(page.body_terms) | page.title_terms)
        count = len(documents)
        self.idf = {t: math.log(1 + (count - df + 0.5) / (df + 0.5)) for t, df in frequency.items()}

    def __len__(self) -> int:
        return len(self.pages)

    def search(self, query: str, limit: int = 8) -> list[Hit]:
        wanted = list(dict.fromkeys(terms(query)))
        if not wanted:
            raise DocsError("the query holds no searchable word")
        hits = []
        for page in self.pages.values():
            score = 0.0
            for term in wanted:
                idf = self.idf.get(term, 0.0)
                tf = page.body_terms.get(term, 0)
                if tf:
                    norm = 1 - B + B * (page.length / self.average if self.average else 1.0)
                    score += idf * tf * (K1 + 1) / (tf + K1 * norm)
                if term in page.title_terms:
                    score += TITLE_BOOST * idf
            if score > 0:
                hits.append(Hit(page, score, snippet(page.markdown, query)))
        hits.sort(key=lambda h: (-h.score, h.page.slug))
        return hits[:limit]

    def page(self, reference: str, base_url: str = DEFAULT_DOCS_URL) -> Page:
        slug = normalize_slug(reference, base_url)
        page = self.pages.get(slug)
        if page is None:
            raise DocsError(f"no page `{reference}` in this documentation: find one with docs_search")
        return page


def normalize_slug(reference: str, base_url: str = DEFAULT_DOCS_URL) -> str:
    """`use/workflows`, `/choregos/use/workflows/`, the page's public URL or `use/workflows.md`: one slug."""
    slug = reference.strip().split("#", 1)[0].split("?", 1)[0]
    base = base_url.rstrip("/")
    if slug.startswith(base):
        slug = slug[len(base) :]
    path = re.sub(r"^https?://[^/]+", "", base)
    if path and (slug == path or slug.startswith(path + "/")):
        slug = slug[len(path) :]
    slug = slug.strip("/")
    for extension in _EXTENSIONS:
        if slug.endswith(extension):
            slug = slug[: -len(extension)]
    if slug in {"index", "README"}:
        return ""
    return slug.removesuffix("/index")


def snippet(markdown: str, query: str) -> str:
    """A short passage around the first word of the query the page contains, on one line."""
    lowered = markdown.lower()
    positions = []
    for word in _WORD.findall(query.lower()):
        if word in _STOPWORDS:
            continue
        found = re.search(rf"\b{re.escape(word[:-1] if len(word) > 3 else word)}", lowered)
        if found:
            positions.append(found.start())
    start = max(0, min(positions) - 60) if positions else 0
    passage = " ".join(markdown[start : start + SNIPPET_SIZE].split())
    return ("…" if start else "") + passage + ("…" if start + SNIPPET_SIZE < len(markdown) else "")


def section(page: Page, anchor: str) -> Section:
    wanted = anchor.strip().lstrip("#")
    for candidate in page.sections:
        if candidate.anchor == wanted:
            return candidate
    anchors = ", ".join(s.anchor for s in page.sections[:40]) or "(none)"
    raise DocsError(f"no section `{anchor}` in `{page.slug or '/'}`; its sections: {anchors}")


def build(root: Path) -> Index:
    """Walks `root` once: regular `.md`/`.mdx` files, never a symbolic link nor a file outside it.

    Docusaurus leaves out files and folders whose name starts with `_` (partials): so does this."""
    resolved = root.resolve()
    pages: list[Page] = []
    for directory, folders, files in os.walk(resolved, followlinks=False):
        folders[:] = sorted(f for f in folders if not f.startswith((".", "_")))
        for name in sorted(files):
            if name.startswith((".", "_")) or not name.endswith(_EXTENSIONS):
                continue
            path = Path(directory) / name
            # A symbolic link that leads out of the directory is not documentation.
            if not path.resolve().is_relative_to(resolved) or not path.is_file():
                continue
            page = _page(path, path.relative_to(resolved))
            if page is not None:
                pages.append(page)
            if len(pages) >= MAX_PAGES:
                return Index(pages)
    return Index(pages)


_INDEXES: dict[str, Index] = {}


def index_of(directory: str) -> Index | None:
    """The index of `directory`, built at the first call; `None` when this build bundles no documentation."""
    root = Path(directory or DEFAULT_DOCS_DIR)
    key = str(root)
    if key in _INDEXES:
        return _INDEXES[key]
    if not root.is_dir():
        return None  # not remembered: an absent directory costs one `stat`
    index = build(root)
    if not len(index):
        return None
    _INDEXES[key] = index
    return index


def read(page: Page, anchor: str | None = None, offset: int = 0) -> tuple[str, Section | None, int | None]:
    """The page's Markdown, or one of its sections, from `offset`, at most `READ_CHUNK` characters.

    Returns the text, the section, and the offset to continue from (`None` at the end). A chunk ends at
    a line break when one is in its second half, so a continuation does not split a line."""
    chosen = section(page, anchor) if anchor else None
    content = page.markdown[chosen.start : chosen.end] if chosen else page.markdown
    if offset < 0 or offset > len(content):
        raise DocsError(f"offset {offset} is outside the text ({len(content)} characters)")
    end = offset + READ_CHUNK
    if end >= len(content):
        return content[offset:], chosen, None
    cut = content.rfind("\n", offset + READ_CHUNK // 2, end)
    end = cut + 1 if cut != -1 else end
    return content[offset:end], chosen, end
