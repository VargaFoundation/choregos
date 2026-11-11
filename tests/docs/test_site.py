"""The public user documentation (`docs/site/`) holds to the code (S62-09a).

`docs/site/` is published on https://docs.varga.foundation/choregos/ (the S62 contract fixes its slugs).
A page written for someone who knows nothing yet is a promise: a screen it names, a command it gives, a
YAML block it shows must exist and work today. This file turns CI red when a page drifts:

- every slug of the contract has its page, and no page overrides its URL;
- every relative link resolves, to a file and to a heading;
- every `yaml` block parses, and the workflows, policies and knowledge-base manifests are validated by
  the platform's own models, with no warning; every `json` block parses;
- every `choregos` command exists in the CLI, with the options the page gives it;
- every screen path written `**A › B**` names labels the console has, and every console address in
  backticks is a page of the console;
- the prose is safe for MDX (the site is Docusaurus 3, which reads `.md` as MDX).

The second half of the contract (build/, integrate/, cli/, host/, reference/) is written by S62-09b. Its
sections are checked as soon as their folder holds a page; until then, and only for those sections, a
missing page and a link to it are tolerated.
"""

from __future__ import annotations

import json
import re
import shlex
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_cli.knowledge import Manifest
from choregos_contracts import Policy
from choregos_core import parse_workflow
from choregos_core.policy import parse_policy

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "docs" / "site"
WEB = ROOT / "apps" / "web" / "src"
CLI_REFERENCE = ROOT / "docs" / "cli.md"

#: The slugs of the S62 contract, in the order of the sidebar.
CONTRACT = [
    "",
    "concepts",
    "get-started",
    "use/projects-and-members",
    "use/workflows",
    "use/work-items",
    "use/decisions",
    "use/knowledge",
    "use/connectors",
    "use/budgets-and-policies",
    "build/skills",
    "build/agents",
    "build/plugins",
    "build/tools",
    "integrate/mcp",
    "integrate/a2a",
    "integrate/triggers",
    "integrate/rest",
    "cli/install",
    "cli/reference",
    "host",
    "reference",
]
#: The top-level sections, in sidebar order: a page (concepts, get-started) or a folder.
SECTIONS = ["concepts", "get-started", "use", "build", "integrate", "cli", "host", "reference"]
#: The sections S62-09b writes: tolerated while their folder holds no page.
SECOND_HALF = {"build", "integrate", "cli", "host", "reference"}

FENCE = re.compile(r"^```([a-z]*)[^\n]*\n(.*?)^```", re.M | re.S)
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
#: `[text](target)` and `![alt](target)`, the target up to a space or the closing parenthesis.
LINK = re.compile(r"(!?)\[[^\]\n]*\]\(\s*<?([^)\s>]+)")
SCREEN_PATH = re.compile(r"\*\*([^*\n]+? › [^*\n]+?)\*\*")
CONSOLE_ADDRESS = re.compile(
    r"`(/(?:p|admin|account|agents|skills|knowledge|memories|library|inbox|integrations|costs|projects)"
    r"(?:/[^`\s]*)?)`"
)
NUMBER_PREFIX = re.compile(r"^\d+-")


# ───────────────────────────── the pages ─────────────────────────────


def _pages() -> list[Path]:
    return sorted(p for p in SITE.rglob("*.md") if p.name != "README.md") + sorted(SITE.rglob("*.mdx"))


def _all_markdown() -> list[Path]:
    return sorted(SITE.rglob("*.md"))


def _url_of(page: Path) -> str:
    parts = [NUMBER_PREFIX.sub("", part) for part in page.relative_to(SITE).with_suffix("").parts]
    if parts[-1] == "index":
        parts = parts[:-1]
    return "/".join(parts)


def _has_pages(section: str) -> bool:
    folder = SITE / section
    return folder.is_dir() and any(folder.rglob("*.md"))


def _front_matter(page: Path) -> dict[str, Any]:
    match = FRONT_MATTER.match(page.read_text(encoding="utf-8"))
    assert match, f"{page.relative_to(ROOT)} has no front matter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict)
    return data


def _blocks(language: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for page in _pages():
        for index, (lang, body) in enumerate(FENCE.findall(page.read_text(encoding="utf-8"))):
            if lang == language:
                found.append((f"{page.relative_to(SITE)}#{index}", body))
    return found


def test_the_folder_has_pages() -> None:
    """Without this guard, moving the folder would leave every test below green and empty."""
    assert len(_pages()) >= 10, "docs/site has fewer pages than the first half of the contract"


@pytest.mark.parametrize("slug", CONTRACT, ids=lambda s: s or "overview")
def test_every_slug_of_the_contract_has_its_page(slug: str) -> None:
    section = slug.split("/", 1)[0]
    if section in SECOND_HALF and not _has_pages(section):
        pytest.skip(f"`{section}/` holds no page yet (S62-09b)")
    urls = {_url_of(page) for page in _pages()}
    assert slug in urls, f"no page publishes /choregos/{slug}"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: str(p.relative_to(SITE)))
def test_a_page_has_a_title_and_a_description_and_keeps_its_url(page: Path) -> None:
    data = _front_matter(page)
    assert data.get("title"), "a page needs a `title`"
    assert data.get("description"), "a page needs a `description`: the sentence search engines show"
    for key in ("slug", "id"):
        assert key not in data, f"`{key}` would move the page away from the contract's URL"


def test_every_section_has_its_category_in_the_contract_order() -> None:
    positions: list[float] = []
    for section in SECTIONS:
        folder = SITE / section
        if folder.is_dir():
            category = json.loads((folder / "_category_.json").read_text(encoding="utf-8"))
            assert category.get("label"), f"{section}/_category_.json has no label"
            positions.append(float(category["position"]))
        else:
            positions.append(float(_front_matter(SITE / f"{section}.md")["sidebar_position"]))
    overview = float(_front_matter(SITE / "index.md")["sidebar_position"])
    assert [overview, *positions] == sorted([overview, *positions]), (
        "the sidebar order is not the contract's: "
        + str(dict(zip(["overview", *SECTIONS], [overview, *positions], strict=True)))
    )
    assert len(set(positions)) == len(positions), "two sections share a position"


# ───────────────────────────── links ─────────────────────────────


def _slug(heading: str) -> str:
    """A heading's anchor, as GitHub and Docusaurus (github-slugger) write it."""
    text = re.sub(r"[`*_]", "", heading.strip()).lower()
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


@cache
def _anchors(path: Path) -> frozenset[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for heading in re.findall(r"^#{1,6} (.+?)\s*#*$", text, re.M):
        base = _slug(heading)
        count = seen.get(base, 0)
        anchors.add(base if count == 0 else f"{base}-{count}")
        seen[base] = count + 1
    return frozenset(anchors)


def _links(page: Path) -> list[str]:
    text = FENCE.sub("", page.read_text(encoding="utf-8"))
    text = INLINE_CODE.sub("", text)
    return [
        target
        for _bang, target in LINK.findall(text)
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not target.startswith("/")
    ]


def _tolerated(target: Path) -> bool:
    """A page of a second-half section that holds no page yet."""
    try:
        relative = target.resolve().relative_to(SITE.resolve())
    except ValueError:
        return False
    section = relative.parts[0] if relative.parts else ""
    return section in SECOND_HALF and not _has_pages(section)


@pytest.mark.parametrize("page", _all_markdown(), ids=lambda p: str(p.relative_to(SITE)))
def test_every_relative_link_resolves_to_a_file_and_a_heading(page: Path) -> None:
    broken: list[str] = []
    for link in _links(page):
        path, _, anchor = link.partition("#")
        target = (page.parent / path).resolve() if path else page.resolve()
        if not target.exists():
            if not _tolerated(target):
                broken.append(link)
            continue
        if not target.is_relative_to(ROOT):
            broken.append(f"{link} (outside the repository)")
        elif anchor and target.suffix == ".md" and anchor not in _anchors(target):
            broken.append(f"{link} (no such heading)")
    assert not broken, f"{page.relative_to(ROOT)}: {broken}"


def test_the_overview_maps_every_section_of_the_contract() -> None:
    linked: set[str] = set()
    for link in _links(SITE / "index.md"):
        target = (SITE / link.partition("#")[0]).resolve()
        if target.is_relative_to(SITE.resolve()) and target.suffix == ".md":
            linked.add(_url_of(SITE / target.relative_to(SITE.resolve())))
    missing = [slug for slug in CONTRACT if slug and slug not in linked]
    assert not missing, f"the overview's map does not link to {missing}"


# ───────────────────────────── examples ─────────────────────────────


def _documents() -> list[tuple[str, str, Any]]:
    return [(name, body, yaml.safe_load(body)) for name, body in _blocks("yaml")]


def test_there_are_examples_of_each_kind() -> None:
    kinds = [doc.get("kind") for _name, _body, doc in _documents() if isinstance(doc, dict)]
    for kind in ("Workflow", "Policy", "KnowledgeBase"):
        assert kind in kinds, f"no `{kind}` example in docs/site: the test checks nothing for it"


@pytest.mark.parametrize(("name", "body"), _blocks("yaml"), ids=lambda v: v if "#" in v else "")
def test_a_yaml_example_is_valid(name: str, body: str) -> None:
    document = yaml.safe_load(body)
    if not isinstance(document, dict):
        return
    kind = document.get("kind")
    if kind == "Workflow":
        _workflow, report = parse_workflow(body, strict=False)
        assert report.valid, f"{name}: " + " · ".join(i.format() for i in report.errors)
        assert not report.warnings, f"{name}: " + " · ".join(i.format() for i in report.warnings)
    elif kind == "Policy":
        parse_policy(body)
        Policy.model_validate(document)
    elif kind == "KnowledgeBase":
        Manifest.model_validate(document)


@pytest.mark.parametrize(("name", "body"), _blocks("json"), ids=lambda v: v if "#" in v else "")
def test_a_json_example_parses(name: str, body: str) -> None:
    json.loads(body)


# ───────────────────────────── commands ─────────────────────────────


@cache
def _cli_sections() -> dict[str, str]:
    """`choregos a b` → the text of its section in the generated reference."""
    text = CLI_REFERENCE.read_text(encoding="utf-8")
    heads = list(re.finditer(r"^#+ `(choregos[a-z0-9 -]*)`\s*$", text, re.M))
    sections: dict[str, str] = {}
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        sections[head.group(1)] = text[head.end() : end]
    return sections


def _commands() -> list[str]:
    commands: list[str] = []
    for _name, body in _blocks("bash"):
        for raw in body.replace("\\\n", " ").splitlines():
            command = raw.split(" #", 1)[0].strip()
            if command and not command.startswith("#"):
                commands.append(command)
    return commands


def _compose_profiles() -> set[str]:
    services = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]
    return {profile for service in services.values() for profile in service.get("profiles", [])}


def test_there_are_commands_to_check() -> None:
    assert sum(c.startswith("choregos ") for c in _commands()) >= 20


@pytest.mark.parametrize("command", _commands())
def test_every_command_exists_with_its_options(command: str) -> None:
    words = shlex.split(command.replace("<", "").replace(">", ""))
    tool = words[0]
    if tool == "choregos":
        known = _cli_sections()
        path = "choregos"
        for word in words[1:]:
            if f"{path} {word}" in known:
                path = f"{path} {word}"
            else:
                break
        usage = known.get(path, "")
        assert "Usage:" in usage, f"no CLI command for `{command}` (`{path}` is not a command)"
        for word in words:
            flag = word.split("=", 1)[0]
            if flag.startswith("-") and flag not in {"-", "--"}:
                assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", usage), (
                    f"`{path}` has no option `{flag}`"
                )
    elif words[:2] == ["docker", "compose"]:
        for index, word in enumerate(words):
            if word == "--profile":
                assert words[index + 1] in _compose_profiles(), f"no compose profile `{words[index + 1]}`"
    elif tool == "cp":
        assert (ROOT / words[1]).exists(), f"`{words[1]}` is not in the repository"
    elif tool not in {"git", "cd", "curl"}:
        pytest.fail(f"a command this test does not know how to check: `{command}`")


# ───────────────────────────── the console ─────────────────────────────


@cache
def _console_words() -> frozenset[str]:
    """Every label the console's source writes: string literals and JSX text."""
    words: set[str] = set()
    for path in WEB.rglob("*.ts*"):
        if path.suffix not in {".ts", ".tsx"} or "mocks" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        words.update(m.strip() for m in re.findall(r'"([^"\n]{1,80})"', text))
        words.update(m.strip() for m in re.findall(r">\s*([^<>{}\n]{1,80}?)\s*<", text))
        words.update(m.strip() for m in re.findall(r">\s*([^<>{}\n]{1,80}?)\s*$", text, re.M))
    return frozenset(words)


def _screen_paths() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for page in _all_markdown():
        text = INLINE_CODE.sub("", FENCE.sub("", page.read_text(encoding="utf-8")))
        found += [(str(page.relative_to(SITE)), path) for path in SCREEN_PATH.findall(text)]
    return found


def test_there_are_screen_paths_to_check() -> None:
    assert len(_screen_paths()) >= 15


@pytest.mark.parametrize(("page", "path"), _screen_paths())
def test_a_screen_path_names_labels_the_console_has(page: str, path: str) -> None:
    missing = [segment for segment in path.split(" › ") if segment.strip() not in _console_words()]
    assert not missing, f"{page}: `{path}` names {missing}, which the console does not write"


@cache
def _console_routes() -> list[list[str]]:
    app = WEB / "app"
    return [list(p.parent.relative_to(app).parts) for p in app.rglob("page.tsx")]


def _is_a_route(address: str) -> bool:
    segments = [s for s in address.split("?", maxsplit=1)[0].split("/") if s]
    for route in _console_routes():
        if len(route) == len(segments) and all(
            (r.startswith("[") and (s.startswith("<") or s.startswith("["))) or r == s
            for r, s in zip(route, segments, strict=True)
        ):
            return True
    return False


def _console_addresses() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for page in _all_markdown():
        text = FENCE.sub("", page.read_text(encoding="utf-8"))
        found += [(str(page.relative_to(SITE)), a) for a in CONSOLE_ADDRESS.findall(text)]
    return found


@pytest.mark.parametrize(("page", "address"), _console_addresses())
def test_a_console_address_is_a_page_of_the_console(page: str, address: str) -> None:
    assert _is_a_route(address), f"{page}: `{address}` is not a page of the console"


def test_the_address_check_tells_a_page_from_a_typo() -> None:
    assert _is_a_route("/p/<project>/settings")
    assert not _is_a_route("/p/<project>/setings")


# ───────────────────────────── MDX ─────────────────────────────


@pytest.mark.parametrize("page", _pages(), ids=lambda p: str(p.relative_to(SITE)))
def test_the_prose_is_safe_for_mdx(page: Path) -> None:
    """A bare `<` opens a JSX tag and a bare `{` an expression: the site's build fails on them."""
    text = FRONT_MATTER.sub("", page.read_text(encoding="utf-8"))
    text = INLINE_CODE.sub("", FENCE.sub("", text))
    unsafe = [
        f"line {number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), 1)
        if re.search(r"<[A-Za-z/!]|[{}]", line)
    ]
    assert not unsafe, f"{page.relative_to(SITE)}: put these in backticks: {unsafe}"
