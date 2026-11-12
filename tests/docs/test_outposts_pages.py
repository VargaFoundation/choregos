# SPDX-License-Identifier: Apache-2.0
"""The two pages about outposts say what the code does (S65-18; ADRs 0147, 0148, 0149).

`docs/outpost-operator.md` is read by someone who may never have seen the platform, to decide
whether to install a program in their own network. `docs/outposts.md` is read by whoever operates
the platform or administers an organisation, to decide whether they want an outpost at all. A
heading that goes missing, a flag the daemon does not have, an exit code it does not use, a key of
the policy file the page forgets: each sends its reader into the wall with confidence. This file
reads the pages and compares them with the daemon's own declarations and with the contract.

The operator's guide:

- carries each section the plan asks for under a heading of its own, and says that an outpost does
  not give data residency before anything else (ADR 0147 §10);
- shows only commands the daemon's command line declares, with the options they are given, and
  lists every option and every environment variable the daemon reads, and no other;
- names every key of the policy file with its default, shows an example the daemon's own loader
  takes, and lists the daemon's refusal codes, its exit codes and the checks of `doctor` as they are;
- names every field of an audit line.

The platform's page offers an organisation's own MCP gateway (ADR 0105) before it describes an
outpost, and points to the page that holds each gesture instead of repeating it.

The daemon's options are read from the Typer command's declaration. Its command line is never run
here: running it sets the logging of the whole process to a stream that is closed once the run
ends, and whatever logs next in the same process would write to it.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts import load_schema
from choregos_contracts.enums import OutpostRefusalCode
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
GUIDE = DOCS / "outpost-operator.md"
PLATFORM = DOCS / "outposts.md"
EXAMPLES = ROOT / "packages" / "contracts" / "schemas" / "examples"

#: A fenced block, at the margin or inside a list item.
FENCE = re.compile(r"^[ \t]*```(?P<lang>[a-z]*)[^\n]*\n(?P<body>.*?)^[ \t]*```[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$", re.M)
INLINE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
DAEMON = "choregos-outpost"

#: What the operator's guide carries, each under a heading (the plan's acceptance criteria). The
#: first one comes before every other section: ADR 0147 §10.
RESIDENCY = "An outpost does not give data residency"
GUIDE_HEADINGS = [
    RESIDENCY,
    "What the daemon does, and what it cannot do",
    "The one firewall rule",
    "The policy file",
    "The door's keys: pinning and rotation",
    "The local audit log",
    "Upgrades are pulled and verified",
    "Who is responsible for what",
]
GATEWAY_FIRST = "Before an outpost: your organisation's own MCP gateway"
WHAT_AN_OUTPOST_IS = "What an outpost is"
#: Each gesture of the platform's page, and the pages that hold its detail.
POINTERS = {
    GATEWAY_FIRST: [
        "adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md",
        "integrations.md#your-organisations-mcp-gateway-adr-0105",
    ],
    "What an outpost does not give": [
        "outpost-operator.md#an-outpost-does-not-give-data-residency",
        "security.md#what-an-outpost-protects-against-and-what-it-does-not",
    ],
    "Switching outposts on": ["deployment.md#outposts-adrs-0147-0149"],
    "Creating an outpost, enrolling and approving an instance": [
        "usage.md#4-write-the-policy",
        "cli.md",
        "outpost-operator.md#enrolment",
    ],
    "Declaring a connector at an outpost": [
        "integrations.md#a-tool-server-at-an-outpost-adr-0149",
        "procedures/connector.mcp.mcp-outpost.md",
    ],
    "Reading its calls": ["outpost-operator.md#the-local-audit-log"],
    "Pausing, revoking, deleting": ["usage.md#4-write-the-policy", "security.md#the-outposts-door"],
    "Rotating the door's key": [
        "deployment.md#outposts-adrs-0147-0149",
        "outpost-operator.md#the-doors-keys-pinning-and-rotation",
    ],
    "What is not there yet": ["adr/0149-a-tool-may-live-at-an-outpost.md"],
    "What is not proven yet": [],
}


# ───────────────────────── reading a page ─────────────────────────


def _text(page: Path) -> str:
    return page.read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """The page without its fenced blocks: what is said, not what is shown."""
    return FENCE.sub("", text)


def _headings(page: Path) -> list[tuple[int, str]]:
    return [(len(marks), title) for marks, title in HEADING.findall(_prose(_text(page)))]


def _section(page: Path, title: str) -> str:
    """A section's own text: from its heading to the next heading of its level or above."""
    text = _text(page)
    found = [m for m in HEADING.finditer(text) if m.group(2) == title]
    assert len(found) == 1, f"{page.name} has {len(found)} heading(s) `{title}`"
    level = len(found[0].group(1))
    later = [m for m in HEADING.finditer(text, found[0].end()) if len(m.group(1)) <= level]
    # A `#` at the start of a line inside a fenced block is a comment, not a heading.
    fenced = [(m.start(), m.end()) for m in FENCE.finditer(text)]
    later = [m for m in later if not any(start <= m.start() < end for start, end in fenced)]
    return text[found[0].end() : later[0].start() if later else len(text)]


def _blocks(text: str, *languages: str) -> list[str]:
    return [m.group("body") for m in FENCE.finditer(text) if m.group("lang") in languages]


def _rows(section: str) -> list[list[str]]:
    """The rows of a section's tables, cell by cell, without their headers."""
    rows: list[list[str]] = []
    for line in _prose(section).splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if "-" in "".join(cells) and set("".join(cells)) <= set(":- "):
            rows.pop()  # the rule under a header: the row before it was that header
        else:
            rows.append(cells)
    return rows


def _first_cells(section: str) -> list[str]:
    """What each row of a section's table is about: the code of its first cell, or the cell itself."""
    cells = []
    for row in _rows(section):
        named = INLINE.search(row[0])
        cells.append(named.group(1) if named else row[0])
    return cells


def _slug(heading: str) -> str:
    """A heading's anchor, as GitHub writes it."""
    text = re.sub(r"[`*_]", "", heading.strip()).lower()
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(page: Path) -> set[str]:
    return {_slug(title) for _level, title in _headings(page)}


def _links(text: str) -> list[str]:
    return [
        target
        for target in LINK.findall(INLINE.sub("", _prose(text)))
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
    ]


# ───────────────────────── the daemon, as it declares itself ─────────────────────────


def _declaration() -> Any:
    """The daemon's command line as Click sees it. Read, never run (see the module's docstring)."""
    import typer
    from choregos_outpost.cli import app

    return typer.main.get_command(app)


def _options(command: Any) -> dict[str, bool]:
    """Each option of a command, and whether it takes a value."""
    import click

    found = dict.fromkeys(command.get_help_option_names(click.Context(command)), False)
    for parameter in command.params:
        for name in [*parameter.opts, *parameter.secondary_opts]:
            found[name] = not getattr(parameter, "is_flag", False)
    return found


def _declared() -> set[tuple[str, str]]:
    """Every option the daemon declares, as the page's table spells it, with the variable it reads:
    `--policy` for an option of the program, `enrol --token` for an option of a command."""
    group = _declaration()
    found = set()
    for prefix, command in [("", group), *((f"{name} ", sub) for name, sub in group.commands.items())]:
        for parameter in command.params:
            found.add((f"{prefix}{parameter.opts[0]}", str(getattr(parameter, "envvar", None) or "")))
    return found


def _fault(words: list[str]) -> str | None:
    """Why the daemon's command line would not take these words, or None when it declares them all."""
    group = _declaration()
    known, command = _options(group), ""
    rest = list(words)
    while rest:
        word = rest.pop(0)
        if word.startswith("-"):
            name, valued, _value = word.partition("=")
            if name not in known:
                return f"{f'`{command}`' if command else 'the daemon'} has no option `{name}`"
            if known[name] and not valued and rest and not rest[0].startswith("-"):
                rest.pop(0)
        elif not command:
            if word not in group.commands:
                return f"the daemon has no command `{word}`"
            command, known = word, _options(group.commands[word])
        else:
            return f"`{command}` takes no argument, and is given `{word}`"
    return None


def _shown_commands(page: Path) -> list[tuple[str, list[str]]]:
    """Every `choregos-outpost …` the page shows: in a command block, also behind another program
    (`kubectl exec … -- choregos-outpost doctor`), and in a sentence. With the words that follow."""
    text = _text(page)
    shown: list[tuple[str, list[str]]] = []
    for block in _blocks(text, "bash", "sh"):
        for raw in block.replace("\\\n", " ").splitlines():
            line = re.sub(r"<[^>\n]*>", "x", raw.split(" #", 1)[0]).strip()
            words = shlex.split(line)
            for index, word in enumerate(words):
                if word == DAEMON:
                    after = words[index + 1 :]
                    stop = next((i for i, w in enumerate(after) if w in {"|", "&&", ";"}), len(after))
                    shown.append((raw.strip(), after[:stop]))
    for span in INLINE.findall(_prose(text)):
        if span == DAEMON or span.startswith(f"{DAEMON} "):
            shown.append((span, shlex.split(span)[1:]))
    return shown


# ───────────────────────── the operator's guide: its sections ─────────────────────────


@pytest.mark.parametrize("title", GUIDE_HEADINGS)
def test_the_guide_carries_each_section_under_its_heading(title: str) -> None:
    assert title in [heading for _level, heading in _headings(GUIDE)], (
        f"docs/outpost-operator.md lost the heading `{title}`: whoever runs an outpost looks for it"
    )
    # What the section says itself, before any sub-section of it.
    said = re.split(r"^#{1,6} ", _prose(_section(GUIDE, title)), maxsplit=1, flags=re.M)[0].strip()
    assert len(said) > 300, f"`{title}` is a heading with nothing under it"


def test_the_guide_says_no_data_residency_before_anything_else() -> None:
    """ADR 0147 §10: "The operator's guide says so before anything else"."""
    sections = [title for level, title in _headings(GUIDE) if level == 2]
    assert sections[0] == RESIDENCY, f"the guide opens on `{sections[0]}`"
    said = _section(GUIDE, RESIDENCY)
    assert "must not be a tool's answer" in said, "the consequence for the operator is gone"


def test_the_three_parties_have_a_column_each() -> None:
    header = next(
        line for line in _section(GUIDE, "Who is responsible for what").splitlines() if line.startswith("|")
    )
    for party in ("The platform's operator", "The organisation's administrator", "The outpost's operator"):
        assert party in header, f"the table of responsibilities has no column for {party.lower()}"
    assert len(_rows(_section(GUIDE, "Who is responsible for what"))) >= 6


# ───────────────────────── the operator's guide: its commands ─────────────────────────


def test_the_guide_shows_commands_to_check() -> None:
    """Without this guard, a page whose fences changed would leave the check below green and empty."""
    shown = [words for _where, words in _shown_commands(GUIDE)]
    assert len(shown) >= 8
    for command in _declaration().commands:
        assert [command] in shown, f"the guide never shows `{DAEMON} {command}`"
    assert any("--" in " ".join(words) for words in shown), "no command of the guide carries an option"
    assert any(where.startswith("kubectl") for where, _words in _shown_commands(GUIDE))


@pytest.mark.parametrize(
    ("where", "words"), _shown_commands(GUIDE), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_command_the_guide_shows_is_one_the_daemon_declares(where: str, words: list[str]) -> None:
    fault = _fault(words)
    assert fault is None, f"docs/outpost-operator.md shows `{where}`: {fault}"


def test_the_command_check_refuses_what_the_daemon_does_not_have() -> None:
    assert _fault(["enrol", "--token-file", "/run/secrets/token", "--no-facts"]) is None
    assert _fault(["--policy", "/etc/policy.yaml", "run", "--grace", "30"]) is None
    assert _fault(["doctor", "--json"]) is None
    assert _fault(["--version"]) is None
    assert "no command `enroll`" in str(_fault(["enroll"]))
    assert "no option `--token-path`" in str(_fault(["enrol", "--token-path", "x"]))
    # An option of one command is not an option of another, nor of the program.
    assert "no option `--json`" in str(_fault(["run", "--json"]))
    assert "no option `--grace`" in str(_fault(["--grace", "5", "run"]))
    assert "takes no argument" in str(_fault(["run", "now"]))


def test_every_flag_the_guide_names_is_one_of_the_daemon_s() -> None:
    """A flag written alone in a sentence (`--no-facts`) is a promise too."""
    group = _declaration()
    known = set(_options(group))
    for command in group.commands.values():
        known |= set(_options(command))
    named = {span for span in INLINE.findall(_prose(_text(GUIDE))) if re.fullmatch(r"--[a-z][a-z-]*", span)}
    assert len(named) >= 5, "the guide names no flag: the check reads nothing"
    assert named <= known, f"the daemon has no {sorted(named - known)}"


def test_the_options_table_is_the_daemon_s_declaration() -> None:
    """Every option and every variable the daemon reads, and no other: an option added to the daemon
    without its line here, or a line left behind, shows up."""
    section = _section(GUIDE, "Running the daemon")
    listed = set()
    for row in _rows(section):
        option = INLINE.search(row[0])
        if option is None or "--" not in option.group(1):
            continue
        variable = INLINE.search(row[1])
        listed.add((option.group(1), variable.group(1) if variable else ""))
    assert listed == _declared(), (
        f"missing from the table: {sorted(_declared() - listed)}; not the daemon's: "
        f"{sorted(listed - _declared())}"
    )
    variables = {variable for _option, variable in _declared() if variable}
    named = set(re.findall(r"\bCHOREGOS_OUTPOST_[A-Z_]+\b", _text(GUIDE)))
    assert named == variables, f"variables the daemon does not read: {sorted(named - variables)}"


def test_the_exit_codes_are_the_daemon_s() -> None:
    from choregos_outpost.cli import CANNOT_START
    from choregos_outpost.daemon import Exit

    listed = {int(code) for code in _first_cells(_section(GUIDE, "Exit codes"))}
    assert listed == {CANNOT_START, *(int(code) for code in Exit)}
    # The two that end the process on purpose are said to loop, with what to do about each.
    said = _prose(_section(GUIDE, "Exit codes"))
    assert "restart it in a loop" in said
    for code in (Exit.UPGRADE_REQUIRED, Exit.REVOKED):
        assert f"- **{int(code)}**:" in said, f"the guide does not say what to do about exit code {int(code)}"


def test_the_checks_of_doctor_are_the_ones_it_runs(tmp_path: Path) -> None:
    from choregos_outpost import doctor
    from choregos_outpost import policy as policies
    from choregos_outpost.settings import Settings

    # On a machine with no policy file, `doctor` still names every check it would have run.
    checks = asyncio.run(doctor.diagnose(Settings(policy=tmp_path / "absent.yaml", state_dir=tmp_path)))
    names = [check.name for check in checks]

    class NoTools:
        async def listing(self, alias: str, *, within_s: float) -> list[Any]:
            return []

    policy = policies.load(EXAMPLES / "outpost-policy.example.yaml")
    alias = next(iter(policy.endpoints))
    per_endpoint = asyncio.run(doctor.endpoint_check(alias, policy, NoTools()))  # type: ignore[arg-type]
    names.append(per_endpoint.name.replace(alias, "<alias>"))
    assert _first_cells(_section(GUIDE, "`doctor`")) == names


# ───────────────────────── the operator's guide: the policy file and the audit log ─────────────────────────


def _policy_keys() -> dict[str, Any]:
    """Every key of the policy file's schema, spelled as the guide's table spells it, with its default."""
    schema = load_schema("outpost-policy.schema.json")
    endpoint, audit = schema["$defs"]["endpoint"], schema["$defs"]["audit"]
    keys: dict[str, Any] = {}
    for name, described in schema["properties"].items():
        if name == "audit":
            keys |= {f"audit.{inner}": None for inner in audit["properties"]}
        else:
            keys[name] = described.get("default")
    for name, described in endpoint["properties"].items():
        keys[f"endpoints.<alias>.{name}"] = described.get("default")
    for name, described in endpoint["properties"]["auth"]["properties"].items():
        keys[f"endpoints.<alias>.auth.{name}"] = described.get("default")
    return keys


def _policy_table() -> str:
    """The policy file's own section, before its sub-sections."""
    return _section(GUIDE, "The policy file").split("\n### ", 1)[0]


def test_the_guide_names_every_key_of_the_policy_file() -> None:
    keys = _policy_keys()
    assert len(keys) >= 13, "the schema lost its keys: the check reads nothing"
    rows = {INLINE.search(row[0]).group(1): row for row in _rows(_policy_table())}  # type: ignore[union-attr]
    assert set(rows) == set(keys), (
        f"keys the guide forgets: {sorted(set(keys) - set(rows))}; keys the schema does not have: "
        f"{sorted(set(rows) - set(keys))}"
    )
    for key, default in keys.items():
        if default is not None:
            said = rows[key][1]
            assert f"`{default}`" in said, f"`{key}` defaults to {default}, the guide says {said}"


def test_the_policy_example_is_one_the_daemon_takes() -> None:
    from choregos_outpost import policy as policies

    (example,) = _blocks(_policy_table(), "yaml")
    policies.parse(example, GUIDE)
    contract = (EXAMPLES / "outpost-policy.example.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(example) == yaml.safe_load(contract), "the example is no longer the contract's"


def test_the_refusals_are_the_contract_s_closed_list() -> None:
    listed = _first_cells(_section(GUIDE, "What the daemon refuses"))
    assert sorted(listed) == sorted(code.value for code in OutpostRefusalCode)


def test_the_guide_names_every_field_of_an_audit_line() -> None:
    schema = load_schema("outpost-audit-line.schema.json")
    section = _section(GUIDE, "The local audit log")
    named = set(INLINE.findall(_prose(section)))
    missing = set(schema["properties"]) - named
    assert not missing, f"fields of an audit line the guide does not name: {sorted(missing)}"
    (line,) = _blocks(section, "json")
    faults = [error.message for error in Draft202012Validator(schema).iter_errors(json.loads(line))]
    assert not faults, f"the audit line the guide shows is not one the daemon writes: {faults}"


# ───────────────────────── the platform's page ─────────────────────────


def test_the_page_offers_the_gateway_before_it_describes_an_outpost() -> None:
    """ADR 0147: "The documentation says so before it describes an outpost"."""
    sections = [title for level, title in _headings(PLATFORM) if level == 2]
    assert GATEWAY_FIRST in sections, f"docs/outposts.md lost the heading `{GATEWAY_FIRST}`"
    assert WHAT_AN_OUTPOST_IS in sections, f"docs/outposts.md lost the heading `{WHAT_AN_OUTPOST_IS}`"
    assert sections.index(GATEWAY_FIRST) < sections.index(WHAT_AN_OUTPOST_IS), (
        "docs/outposts.md describes an outpost before it offers the organisation's own MCP gateway"
    )
    assert sections[0] == GATEWAY_FIRST, f"the gateway comes after `{sections[0]}`"
    assert "need no outpost" in _section(PLATFORM, GATEWAY_FIRST)


@pytest.mark.parametrize("title", POINTERS)
def test_each_gesture_points_to_the_page_that_holds_it(title: str) -> None:
    links = _links(_section(PLATFORM, title))
    missing = [target for target in POINTERS[title] if target not in links]
    assert not missing, f"`{title}` no longer points to {missing}"


def test_the_page_repeats_no_table_of_settings() -> None:
    """The settings and the chart's values are `docs/deployment.md`'s: one place to keep true."""
    text = _prose(_text(PLATFORM))
    assert not [line for line in text.splitlines() if line.lstrip().startswith("|")]
    assert set(re.findall(r"\bCHOREGOS_[A-Z_]+\b", text)) <= {"CHOREGOS_OUTPOSTS"}
    assert not _blocks(_text(PLATFORM), "yaml", "bash")


def test_the_platform_s_commands_are_in_the_cli_reference() -> None:
    reference = _text(DOCS / "cli.md")
    heads = list(re.finditer(r"^#+ `(choregos[a-z0-9 -]*)`\s*$", reference, re.M))
    usage = {
        head.group(1): reference[head.end() : heads[i + 1].start() if i + 1 < len(heads) else None]
        for i, head in enumerate(heads)
    }
    shown = [span for span in INLINE.findall(_prose(_text(PLATFORM))) if span.startswith("choregos outposts")]
    assert len(shown) >= 2, "the page names no command of `choregos outposts`"
    for span in shown:
        # The longest command the reference has for these words; a placeholder is not a word.
        path, rest = "choregos", shlex.split(re.sub(r"<[^>]*>", "", span))[1:]
        while rest and f"{path} {rest[0]}" in usage:
            path = f"{path} {rest.pop(0)}"
        unknown = [word for word in rest if not word.startswith("-")]
        assert path != "choregos" and not unknown, f"no CLI command for `{span}` ({unknown})"
    flags = {span for span in INLINE.findall(_prose(_section(PLATFORM, "Reading its calls"))) if "--" in span}
    for flag in flags:
        assert flag.split()[0] in usage["choregos outposts calls"], f"`calls` has no option `{flag}`"


@pytest.mark.parametrize("page", [GUIDE, PLATFORM], ids=lambda p: p.name)
def test_every_link_of_the_two_pages_names_a_heading_that_exists(page: Path) -> None:
    """`test_links.py` checks the file; a link between these pages also names a section."""
    broken = []
    for target in _links(_text(page)):
        path, _, anchor = target.partition("#")
        linked = (page.parent / path) if path else page
        if not linked.exists() or (anchor and linked.suffix == ".md" and anchor not in _anchors(linked)):
            broken.append(target)
    assert not broken, f"{page.name} links to nothing: {broken}"


# ───────────────────────── the lines elsewhere ─────────────────────────


@pytest.mark.parametrize(
    ("page", "heading", "targets"),
    [
        ("deployment.md", "Outposts (ADRs 0147-0149)", ["outposts.md", "outpost-operator.md"]),
        (
            "security.md",
            "What an outpost protects against, and what it does not",
            ["outposts.md", "outpost-operator.md"],
        ),
        ("concepts.md", "Outposts", ["outposts.md"]),
        ("positioning.md", "Against the tool brokers", ["outposts.md"]),
    ],
)
def test_the_reference_pages_point_to_the_two_pages(page: str, heading: str, targets: list[str]) -> None:
    links = _links(_section(DOCS / page, heading))
    missing = [target for target in targets if target not in links]
    assert not missing, f"docs/{page}, `{heading}`, no longer points to {missing}"


def test_security_says_what_an_outpost_does_not_give() -> None:
    said = _section(DOCS / "security.md", "What an outpost protects against, and what it does not")
    assert "what the local policy allows, and no more" in said
    assert "does not give data residency" in said


def test_the_index_and_the_public_site_lead_to_the_page() -> None:
    index = _links(_text(DOCS / "README.md"))
    assert "outposts.md" in index and "outpost-operator.md" in index
    # The public site has no page for outposts: one line under "Go further", after the gateway's.
    further = _links(_section(DOCS / "site" / "build" / "tools.md", "Go further"))
    gateway = "../../integrations.md#your-organisations-mcp-gateway-adr-0105"
    assert "../../outposts.md" in further
    assert further.index(gateway) < further.index("../../outposts.md"), "the gateway is offered first"
    assert not list((DOCS / "site").rglob("*outpost*.md")), "an outposts page on the site is a decision"
