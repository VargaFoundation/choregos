"""Every `choregos` command of `docs/site/use/terminal.md` parses against the real CLI (S67-13).

`test_site.py` checks that a command's group and its options appear in the generated reference. That is
loose: a misspelt sub-command (`choregos decide aprove ID`) passes it as a positional argument. This test
walks the real Typer application instead: each word that names a command must be one, each option must
belong to the command it follows, and the arguments the command requires must be there. Nothing is
executed and no network call is made: click parses the line (`make_context`) and stops.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import typer.core
import typer.main
from choregos_cli.main import app

# Typer 0.2x ships its own copy of click: the errors and groups of the application are its.
from typer._click.core import Context
from typer._click.exceptions import UsageError

PAGE = Path(__file__).resolve().parents[2] / "docs" / "site" / "use" / "terminal.md"
BLOCK = re.compile(r"^```bash\n(.*?)^```", re.M | re.S)
REDIRECTS = {">", ">>", "2>", "2>&1", "<"}


def _lines(text: str) -> list[list[str]]:
    """The `choregos` commands of the page's bash blocks, as argument lists (`choregos` itself dropped)."""
    found: list[list[str]] = []
    for block in BLOCK.findall(text):
        for raw in block.replace("\\\n", " ").splitlines():
            command = raw.split(" #", 1)[0].strip()
            for segment in command.split(" | "):
                words = shlex.split(segment)
                while words and re.fullmatch(r"[A-Z_]+=\S*", words[0]):
                    words = words[1:]
                if words[:1] != ["choregos"]:
                    continue
                for index, word in enumerate(words):
                    if word in REDIRECTS:
                        words = words[:index]
                        break
                found.append(words[1:])
    return found


def check(args: list[str]) -> None:
    """Parse `choregos ARGS` against the real application; raise `UsageError` when it does not fit."""
    root = typer.main.get_command(app)
    command = root
    name = "choregos"
    rest = list(args)
    while True:
        if not isinstance(command, typer.core.TyperGroup):
            command.make_context(name, list(rest))  # the options and the arguments the command requires
            return
        # A group takes its own options (`--no-color`), then names a sub-command: split them with its parser.
        context = Context(command, info_name=name)
        _options, leftover, _order = command.make_parser(context).parse_args(args=list(rest))
        if not leftover:
            return  # a group alone: its help, or the workspace
        word, rest = leftover[0], leftover[1:]
        sub = command.get_command(context, word)
        if sub is None:
            raise UsageError(f"`{name}` has no command `{word}`")
        command, name = sub, f"{name} {word}"


def test_the_page_has_commands_to_check() -> None:
    """Without this guard, a moved page or a renamed fence leaves the test green and empty."""
    commands = _lines(PAGE.read_text(encoding="utf-8"))
    assert len(commands) >= 25
    for needed in (["ask", "-"], ["login", "--fresh"], ["decide", "approve", "ID"], ["activity", "--run"]):
        assert any(c[: len(needed) - 1] == needed[:-1] for c in commands), f"nothing about {needed}"


@pytest.mark.parametrize("args", _lines(PAGE.read_text(encoding="utf-8")), ids=" ".join)
def test_every_command_of_the_page_parses(args: list[str]) -> None:
    check(args)


@pytest.mark.parametrize(
    "args",
    [
        ["decide", "aprove", "ID"],  # a misspelt sub-command
        ["activity", "--wach"],  # a misspelt option
        ["artifacts", "download", "--force"],  # a missing argument
        ["ask", "--continu", "hello"],
        ["--no-colour", "activity"],
        ["chat", "--org"],  # an option that needs a value
    ],
    ids=" ".join,
)
def test_the_check_refuses_what_is_misspelt(args: list[str]) -> None:
    """The guard of the test above: a check that accepted these would prove nothing."""
    with pytest.raises(UsageError):
        check(args)


def test_a_misspelt_command_in_the_page_would_be_found() -> None:
    text = PAGE.read_text(encoding="utf-8").replace("choregos decide approve ID", "choregos decide aprove ID")
    broken = []
    for args in _lines(text):
        try:
            check(args)
        except UsageError:
            broken.append(args)
    assert broken == [["decide", "aprove", "ID"]]
