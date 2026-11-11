# SPDX-License-Identifier: Apache-2.0
"""The public pages of S62-09b (`docs/site/build`, `integrate`, `cli`, `host`, `reference`) stay true.

They are published to https://docs.varga.foundation/choregos/ and read by people who know nothing of
the repository: a command that does not exist, a YAML example the parser refuses, a link to nothing or
a key of the workflow schema the reference forgets would send them into the wall. Each check below
reads the pages as they are and compares them with the code:

- the fixed slugs of the shared contract exist, each page has its front matter and one title;
- relative links resolve inside `docs/site`, anchors included; links to the repository name files and
  headings that exist; site-absolute links name a slug of the contract;
- the pages are safe for MDX (Docusaurus 3): no bare `<` or `{` outside code;
- every `choregos` command parses against the CLI's own Typer app (options, arguments);
- every YAML block is claimed by a check: a workflow parses without error or warning, a policy
  validates, Helm values name keys of the chart, the plugin of the plugins page validates;
- the workflow reference names every key of the workflow schema and lists exactly the known gates;
- the CLI reference page is the generator's output.

The requests themselves are sent to the API by `apps/api/tests/test_site_*`, and the code agent of the
agents page is run by `packages/agent-sdk/tests/test_site_code_agent.py`.
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

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "docs" / "site"
SECTIONS = ("build", "integrate", "cli", "host", "reference")
PAGES = sorted(p for section in SECTIONS for p in (SITE / section).rglob("*.md"))
#: The slugs of the shared contract this story owns (`/choregos/<slug>`), and the others it may link to.
OWN_SLUGS = {
    "build/skills": "build/skills.md",
    "build/agents": "build/agents.md",
    "build/plugins": "build/plugins.md",
    "build/tools": "build/tools.md",
    "integrate/mcp": "integrate/mcp.md",
    "integrate/a2a": "integrate/a2a.md",
    "integrate/triggers": "integrate/triggers.md",
    "integrate/rest": "integrate/rest.md",
    "cli/install": "cli/install.md",
    "cli/reference": "cli/reference.md",
    "host/": "host/index.md",
    "reference/": "reference/index.md",
}
CONTRACT_SLUGS = {
    "",
    "concepts/",
    "get-started/",
    "use/projects-and-members",
    "use/workflows",
    "use/work-items",
    "use/decisions",
    "use/knowledge",
    "use/connectors",
    "use/budgets-and-policies",
    *OWN_SLUGS,
}
#: The pages whose plugin files (titled blocks) are written to disk and loaded as a plugin by the tests.
PLUGIN_PAGES = {"build/plugins.md", "build/assistants.md"}
FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.M | re.S)
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
LANGUAGES = {"bash", "shell", "powershell", "json", "yaml", "python", "markdown", "text", "mermaid"}
#: Commands a page may announce before the CLI has them, each with the story that brings it. The page
#: must mark them with an "Available from" note.
UPCOMING = {"choregos self update": "S62-08, the CLI served by the instance"}


def _rel(page: Path) -> str:
    return page.relative_to(SITE).as_posix()


def _text(page: Path) -> str:
    return page.read_text(encoding="utf-8")


def _body(page: Path) -> str:
    """The page without its front matter."""
    text = _text(page)
    if text.startswith("---\n"):
        return text.split("\n---\n", 1)[1]
    return text


def _blocks(page: Path, *languages: str) -> list[tuple[str, str]]:
    """(info string, content) of each fenced block, filtered by language."""
    found = [(info.strip(), content) for info, content in FENCE.findall(_body(page))]
    return [(info, content) for info, content in found if not languages or info.split(" ")[0] in languages]


def _prose(page: Path) -> str:
    """The page outside its code: fences and inline code removed."""
    text = FENCE.sub("", _body(page))
    return re.sub(r"(`+)(?:(?!\1).)+?\1", "", text, flags=re.S)


def _slug(heading: str) -> str:
    """The anchor Docusaurus (github-slugger) gives a heading."""
    text = heading.strip().lower().replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    text = FENCE.sub("", text)
    return {_slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}


def test_there_are_pages_to_check() -> None:
    assert len(PAGES) >= 20, "the pages moved: these checks would check nothing"


@pytest.mark.parametrize("slug", sorted(OWN_SLUGS))
def test_the_contract_s_slugs_exist(slug: str) -> None:
    assert (SITE / OWN_SLUGS[slug]).is_file(), f"/choregos/{slug} has no page"


@pytest.mark.parametrize("section", SECTIONS)
def test_each_section_has_its_category(section: str) -> None:
    """The positions are S62-09a's (`tests/docs/test_site.py` checks their order); a label is needed here."""
    category = json.loads((SITE / section / "_category_.json").read_text(encoding="utf-8"))
    assert category.get("label")


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_a_page_has_its_front_matter_and_one_title(page: Path) -> None:
    text = _text(page)
    assert text.startswith("---\n"), "front matter first"
    meta = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
    assert meta.get("title") and meta.get("description"), meta
    titles = re.findall(r"^# .+$", FENCE.sub("", _body(page)), re.M)
    assert len(titles) == 1, titles


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_links_resolve(page: Path) -> None:
    """A relative link names a file of the repository and one of its headings. Inside `docs/site` it is
    another page; outside, the import into the documentation site rewrites it to GitHub at the release
    tag. A site-absolute link names a slug of the contract."""
    broken: list[str] = []
    for target in LINK.findall(FENCE.sub("", _body(page))):
        path, _, anchor = target.partition("#")
        if target.startswith("#"):
            if anchor not in _anchors(_text(page)):
                broken.append(target)
        elif target.startswith("/choregos/"):
            if path.removeprefix("/choregos/") not in CONTRACT_SLUGS:
                broken.append(target)
        elif re.match(r"^[a-z]+:", target):
            if target.startswith("https://github.com/VargaFoundation/choregos/blob/"):
                broken.append(f"{target} (link to the repository with a relative path)")
        else:
            resolved = (page.parent / path).resolve()
            missing = not resolved.exists() or ROOT.resolve() not in resolved.parents
            if missing or (anchor and anchor not in _anchors(resolved.read_text(encoding="utf-8"))):
                broken.append(target)
    assert not broken, f"{_rel(page)}: links to nothing: {broken}"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_the_pages_pin_one_released_version() -> None:
    """Every command that names a release (the chart, the wheels) names the same one, not a future one."""
    current = re.search(r'^version = "([^"]+)"', (ROOT / "packages/cli/pyproject.toml").read_text(), re.M)
    assert current
    pinned = set()
    for page in PAGES:
        pinned |= set(re.findall(r"--version (\d+\.\d+\.\d+)", _text(page)))
        pinned |= set(re.findall(r"choregos_[a-z_]+-(\d+\.\d+\.\d+)-py3", _text(page)))
    assert len(pinned) == 1, f"the pages pin several versions: {sorted(pinned)}"
    assert _version(next(iter(pinned))) <= _version(current.group(1))


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_a_page_is_safe_for_mdx(page: Path) -> None:
    """Docusaurus 3 reads Markdown as MDX: a bare `<word` is a JSX tag and a `{` an expression."""
    prose = _prose(page)
    tags = re.findall(r"<[A-Za-z/!][^\n]{0,30}", prose)
    braces = re.findall(r"[{}][^\n]{0,30}", prose)
    assert not tags and not braces, f"{_rel(page)}: put these in backticks: {tags + braces}"


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_code_blocks_name_their_language(page: Path) -> None:
    unknown = [info for info, _ in _blocks(page) if info.split(" ")[0] not in LANGUAGES]
    assert not unknown, unknown


# ───────────────────────── commands ─────────────────────────


def _commands(page: Path) -> list[str]:
    """Each command of the page's shell blocks: continued lines joined, a quoted body kept whole."""
    commands: list[str] = []
    for _info, block in _blocks(page, "bash", "shell", "powershell"):
        pending = ""
        for raw in block.replace("\\\n", " ").splitlines():
            pending = f"{pending}\n{raw}" if pending else raw.strip()
            try:
                shlex.split(pending)
            except ValueError:
                continue  # an open quote: the command goes on on the next line
            if pending and not pending.startswith("#"):
                commands.append(pending)
            pending = ""
        assert not pending, f"{_rel(page)}: a shell block ends inside a quote"
    return commands


def _all_commands() -> list[tuple[str, str]]:
    return [(_rel(page), command) for page in PAGES for command in _commands(page)]


@cache
def _cli() -> Any:
    import os

    os.environ["TYPER_USE_RICH"] = "0"
    import typer.main
    from choregos_cli.main import app

    return typer.main.get_command(app)


def _cli_problem(words: list[str]) -> str | None:
    """None when `choregos <words>` is a command the CLI parses, else why not."""
    command = _cli()
    path = ["choregos"]
    rest = list(words[1:])
    while getattr(command, "commands", None) is not None:
        if not rest or rest[0] not in command.commands:
            return f"`{' '.join(path)}` has no subcommand `{rest[0] if rest else ''}`"
        path.append(rest[0])
        command = command.commands[rest.pop(0)]
    context = command.make_context(" ".join(path), [], resilient_parsing=True)
    parser = command.make_parser(context)
    try:
        values, extra, _ = parser.parse_args(list(rest))
    except Exception as error:
        return f"`{' '.join(path)}`: {error}"
    if extra:
        return f"`{' '.join(path)}`: unexpected arguments {extra}"
    for param in command.params:
        if param.required and values.get(param.name) is None and not param.envvar:
            if getattr(param, "prompt", None):
                continue
            return f"`{' '.join(path)}`: `{param.name}` is required"
    return None


#: The first word of a command that is not Choregos's, and that a reader runs as such.
KNOWN_TOOLS = {
    "curl",
    "jq",
    "export",
    "helm",
    "kubectl",
    "claude",
    "openssl",
    "python3",
    "pip",
    "uv",
    "zip",
    "cd",
    "sha256sum",
    "chmod",
    "mv",
    "sh",
    "irm",
    "iex",
    "git",
    "docker",
}


@pytest.mark.parametrize(("page", "command"), _all_commands())
def test_every_command_exists(page: str, command: str) -> None:
    lexer = shlex.shlex(re.sub(r"<[^<>]+>", "X", command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    parts: list[list[str]] = [[]]
    for token in lexer:
        if token in {"|", "&&", ";"}:
            parts.append([])
        else:
            parts[-1].append(token)
    for words in parts:
        if not words:
            continue
        if words[0] == "choregos":
            name = " ".join(w for w in words[:3] if not w.startswith("-"))
            problem = _cli_problem(words)
            if problem and name in UPCOMING:
                text = (SITE / page).read_text(encoding="utf-8")
                assert ":::info Available from" in text, f"{page}: `{name}` is not marked as upcoming"
                continue
            assert problem is None, f"{page}: {problem}"
        else:
            assert words[0] in KNOWN_TOOLS, f"{page}: a command this test does not know: `{' '.join(words)}`"


def test_the_command_check_refuses_what_the_cli_does_not_have() -> None:
    """The guard itself: an unknown subcommand, an unknown option, a missing argument."""
    assert _cli_problem(["choregos", "machines", "launch"])
    assert _cli_problem(["choregos", "a2a", "send", "x", "--colour", "red"])
    assert _cli_problem(["choregos", "machines", "create", "acme:billing-api"])
    assert _cli_problem(["choregos", "a2a", "send", "--text", "x"])  # the skill argument is required
    assert _cli_problem(["choregos", "a2a", "send", "billing-api/ship-a-change", "--text", "x"]) is None


# ───────────────────────── YAML and JSON ─────────────────────────


def _values_paths(document: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(document, dict) and document:
        return [p for key, value in document.items() for p in _values_paths(value, (*prefix, key))]
    return [prefix]


@cache
def _chart_values() -> dict[str, Any]:
    values = yaml.safe_load((ROOT / "charts/choregos/values.yaml").read_text(encoding="utf-8"))
    for chart in (ROOT / "charts/choregos/charts").iterdir():
        own = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8")) or {}
        merged = values.setdefault(f"choregos-{chart.name}", {}) or {}
        values[f"choregos-{chart.name}"] = {**own, **merged}
    return dict(values)


def _in_chart(path: tuple[str, ...]) -> bool:
    node: Any = _chart_values()
    for index, key in enumerate(path):
        if not isinstance(node, dict) or key not in node:
            # A map the chart leaves open (`clients: {}`, `agents: {}`) takes any key below it.
            return index > 0 and isinstance(node, dict) and node == {}
        node = node[key]
        if node is None or node == {}:
            return True
    return True


def _yaml_blocks() -> list[tuple[str, str, str]]:
    return [(_rel(page), info, block) for page in PAGES for info, block in _blocks(page, "yaml")]


@pytest.mark.parametrize(("page", "info", "block"), _yaml_blocks())
def test_every_yaml_block_is_checked(page: str, info: str, block: str) -> None:
    from choregos_contracts import Policy
    from choregos_core import parse_workflow

    document = yaml.safe_load(block)
    assert isinstance(document, dict), f"{page}: a YAML block is not a mapping"
    kind = document.get("kind")
    if kind == "Workflow":
        _workflow, report = parse_workflow(block, strict=False)
        assert report.valid, f"{page}: " + " · ".join(i.format() for i in report.errors)
        assert not report.warnings, f"{page}: " + " · ".join(i.format() for i in report.warnings)
    elif kind == "Policy":
        Policy.model_validate(document)
    elif kind in {"Plugin", "Assistant", "conversation"} or {"catalogue", "agent"} <= set(document):
        assert page in PLUGIN_PAGES, f"{page}: a plugin file outside the pages that are validated as a plugin"
        assert "title=" in info, f'{page}: a plugin file must say its path (title="plugin/file.yaml")'
    elif set(document) & {"global", "choregos-api", "choregos-web"}:
        missing = [".".join(p) for p in _values_paths(document) if not _in_chart(p)]
        assert not missing, f"{page}: keys the chart does not have: {missing}"
    elif {"metadata", "a2a"} <= set(document):
        assert page == "integrate/a2a.md", "the opt-in is sent to the door by test_site_a2a_page.py"
    else:
        pytest.fail(f"{page}: a YAML block no check claims: {sorted(document)}")


def test_the_values_check_refuses_a_key_the_chart_does_not_have() -> None:
    assert _in_chart(("global", "oidc", "issuer"))
    assert _in_chart(("global", "mcp", "oauth", "clients", "claude-code", "clientId"))
    assert _in_chart(("choregos-api", "ingress", "hostnames"))
    assert not _in_chart(("global", "oidc", "issuerUrl"))
    assert not _in_chart(("global", "databse", "host"))


@pytest.mark.parametrize("page", PAGES, ids=_rel)
def test_every_json_block_is_json(page: Path) -> None:
    for _, block in _blocks(page, "json"):
        json.loads(block)


def test_the_plugin_validates(tmp_path: Path) -> None:
    """The plugins page's files, written where their titles say, are a valid plugin."""
    from choregos_core.plugins import load_directory

    page = SITE / "build" / "plugins.md"
    written = 0
    for info, block in _blocks(page):
        title = re.search(r'title="([^"]+)"', info)
        if title:
            target = tmp_path / title.group(1)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block, encoding="utf-8")
            written += 1
    assert written == 3, "the page no longer shows the plugin's three files"
    plugin = load_directory(tmp_path / "billing-team")
    assert plugin.name == "billing-team" and plugin.version == "1.0.0"
    assert plugin.digest.startswith("sha256:")


def _write_titled_files(page: Path, root: Path) -> int:
    written = 0
    for info, block in _blocks(page):
        title = re.search(r'title="([^"]+)"', info)
        if title:
            target = root / title.group(1)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block, encoding="utf-8")
            written += 1
    return written


def test_the_assistant_plugin_validates(tmp_path: Path) -> None:
    """The assistants page's files (plugin, agent, assistant, conversation suite) are a valid plugin."""
    from choregos_core.plugins import load_directory

    assert _write_titled_files(SITE / "build" / "assistants.md", tmp_path) == 4
    plugin = load_directory(tmp_path / "billing-assistant")
    assert plugin.name == "billing-assistant"
    assert plugin.digest.startswith("sha256:")


def test_the_assistant_plugin_check_refuses_a_wrong_example(tmp_path: Path) -> None:
    """The guard bites: an assistant whose agent is not in the plugin is refused."""
    from choregos_core.plugins import load_directory

    _write_titled_files(SITE / "build" / "assistants.md", tmp_path)
    manifest = tmp_path / "billing-assistant" / "assistants" / "billing-assistant.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("agent: invoice-helper", "agent: nobody")
    )
    with pytest.raises(Exception, match="nobody"):
        load_directory(tmp_path / "billing-assistant")


# ───────────────────────── the references against the code ─────────────────────────


def _schema_keys() -> set[str]:
    schema = json.loads(
        (ROOT / "packages/contracts/schemas/workflow.schema.json").read_text(encoding="utf-8")
    )
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            keys.update(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return keys


def test_the_workflow_reference_names_every_key_of_the_schema() -> None:
    page = _text(SITE / "reference" / "workflow-yaml.md")
    named = set(re.findall(r"`([A-Za-z0-9_]+)", page)) | set(re.findall(r"\.([a-z_]+)`", page))
    missing = sorted(_schema_keys() - named)
    assert not missing, f"keys of workflow.schema.json the reference does not name: {missing}"


def test_the_workflow_reference_lists_exactly_the_known_gates() -> None:
    from choregos_core.gates import is_async_gate, known_gates

    page = _text(SITE / "reference" / "workflow-yaml.md")
    table = page.split("## Gates", 1)[1].split("\n## ", 1)[0]
    rows = dict(re.findall(r"^\| `([a-z_]+)` \| (.+) \|$", table, re.M))
    assert set(rows) == set(known_gates())
    for name, text in rows.items():
        assert ("(waits)" in text) == is_async_gate(name), name


def test_the_validation_codes_the_pages_name_exist() -> None:
    sources = "".join(
        p.read_text(encoding="utf-8")
        for base in ("packages/core/src", "apps/api/src")
        for p in (ROOT / base).rglob("*.py")
    )
    named: set[str] = set()
    for page in ("reference/errors.md", "reference/workflow-yaml.md"):
        named |= set(
            re.findall(
                r"`((?:workflow|state|actor|transition|gate|action|decision|yaml|schema|skill)\.[a-z_]+)`",
                _text(SITE / page),
            )
        )
    assert len(named) > 20
    missing = sorted(code for code in named if f'"{code}"' not in sources)
    assert not missing, f"codes no source emits: {missing}"


def test_the_mcp_page_has_a_section_per_client_of_the_integrations_page() -> None:
    source = (ROOT / "apps/web/src/lib/integrations.ts").read_text(encoding="utf-8")
    labels = re.findall(r'label: "([^"]+)"', source.split("export const CLIENTS", 1)[1].split("];", 1)[0])
    headings = re.findall(r"^### (.+)$", _text(SITE / "integrate" / "mcp.md"), re.M)
    missing = [
        label
        for label in labels
        if label not in {"CLI", "REST API"} and not any(label.lower() in h.lower() for h in headings)
    ]
    assert not missing and len(labels) >= 7, f"clients of the Integrations page without a section: {missing}"


def test_the_cli_reference_page_is_the_generator_s_output() -> None:
    """`tools/gen_cli_reference.py --check` compares both generated pages; this one must be among them."""
    import os
    import subprocess
    import sys

    tool = ROOT / "tools" / "gen_cli_reference.py"
    assert 'SITE_TARGET = ROOT / "docs" / "site" / "cli" / "reference.md"' in tool.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(tool), "--check"],
        capture_output=True,
        text=True,
        env={**os.environ, "COLUMNS": "100"},
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "docs/site/cli/reference.md" in result.stdout
