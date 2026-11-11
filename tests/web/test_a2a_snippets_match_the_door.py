"""S62-11: the A2A examples the console shows name only methods the door serves and commands the CLI has.

The Integrations page builds its `curl`, A2A 1.0 and CLI snippets from one module
(`apps/web/src/lib/a2a-snippets.ts`), and lists every name they use in `apps/web/src/lib/a2a-names.json`;
`apps/web/tests/a2a-snippets.test.ts` holds the snippets to that list. This test holds the list to the
door's method tables (`choregos_api.a2a.v1.METHODS`, its 0.3.0 values, and `door.py`, which dispatches
them) and to the CLI's typer app: a method renamed in the door, or a command or option renamed in the CLI,
turns the suite red before a user pastes a call that cannot work.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import typer.main

ROOT = pathlib.Path(__file__).resolve().parents[2]
NAMES = json.loads((ROOT / "apps" / "web" / "src" / "lib" / "a2a-names.json").read_text(encoding="utf-8"))
DOOR = ROOT / "apps" / "api" / "src" / "choregos_api" / "a2a" / "door.py"


def _command(path: list[str]) -> Any:
    """`choregos a2a send` as the CLI builds it (typer's own click, so read by its attributes)."""
    from choregos_cli.main import app

    command: Any = typer.main.get_command(app)
    for word in path:
        commands = getattr(command, "commands", None)
        assert isinstance(commands, dict), f"`choregos {' '.join(path)}`: {word} has no subcommands"
        found = commands.get(word)
        assert found is not None, f"`choregos {' '.join(path)}` is not a command of the CLI"
        command = found
    return command


@pytest.mark.parametrize("method", NAMES["methods_0_3"])
def test_each_0_3_method_shown_is_served_by_the_door(method: str) -> None:
    from choregos_api.a2a import v1

    assert method in set(v1.METHODS.values()), f"{method} is not a 0.3.0 method of the door's table"
    assert f'"{method}"' in DOOR.read_text(encoding="utf-8"), f"door.py does not dispatch {method}"


@pytest.mark.parametrize("method", NAMES["methods_1_0"])
def test_each_1_0_method_shown_is_served_by_the_door(method: str) -> None:
    from choregos_api.a2a import v1

    assert method in v1.METHODS, f"{method} is not an A2A 1.0 method the door serves"
    assert method not in v1.UNSERVED


def test_the_two_versions_show_the_same_calls() -> None:
    """Each 1.0 method shown is served as one of the 0.3.0 methods shown: the tabs say the same thing."""
    from choregos_api.a2a import v1

    assert sorted(v1.METHODS[m] for m in NAMES["methods_1_0"]) == sorted(NAMES["methods_0_3"])


@pytest.mark.parametrize("entry", NAMES["commands"], ids=lambda e: " ".join(e["command"]))
def test_each_command_and_option_shown_exists_in_the_cli(entry: dict[str, Any]) -> None:
    command = _command(entry["command"])
    options = {name for parameter in command.params for name in getattr(parameter, "opts", [])}
    for option in entry["options"]:
        assert option in options, f"`choregos {' '.join(entry['command'])}` has no {option} option"


def test_the_token_variable_shown_is_the_one_the_a2a_commands_read() -> None:
    send = _command(["a2a", "send"])
    token = next(p for p in send.params if "--token" in getattr(p, "opts", []))
    assert token.envvar == "CHOREGOS_A2A_TOKEN"
    assert "CHOREGOS_A2A_TOKEN" in NAMES["env"]
    client = (ROOT / "packages" / "cli" / "src" / "choregos_cli" / "client.py").read_text(encoding="utf-8")
    for variable in set(NAMES["env"]) - {"CHOREGOS_A2A_TOKEN"}:
        assert f'"{variable}"' in client, f"the CLI's profile does not read {variable}"
