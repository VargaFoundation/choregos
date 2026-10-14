"""Every command of `docs/getting-started.md` exists (S28-05).

The page used to send people to `make dev-up`, whose script pointed to a `docs/dev.md` that did
not exist. A command in a getting-started page is a promise made to someone who knows nothing
yet: each one is checked here against what the repository actually has — a Make target, a
compose profile, a file, a CLI command.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "getting-started.md"
BLOCK = re.compile(r"```bash\n(.*?)```", re.S)


def _commands() -> list[str]:
    commands: list[str] = []
    for block in BLOCK.findall(PAGE.read_text(encoding="utf-8")):
        # A command continued with `\` is one command.
        for raw in block.replace("\\\n", " ").splitlines():
            command = raw.split(" #", 1)[0].strip()
            if command and not command.startswith("#"):
                commands.append(command)
    return commands


def _make_targets() -> set[str]:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([a-zA-Z0-9_-]+):", makefile, re.M))


def _compose_profiles() -> set[str]:
    services = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]
    return {profile for service in services.values() for profile in service.get("profiles", [])}


def _cli_commands() -> set[str]:
    reference = (ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    return set(re.findall(r"^#+ `(choregos [a-z -]+)`", reference, re.M))


def test_the_page_has_commands_to_check() -> None:
    """Without this guard, moving the page or its fences would leave the test green and empty."""
    commands = _commands()
    assert "docker compose up" in commands
    assert len(commands) > 10


def test_the_page_no_longer_sends_anyone_to_the_missing_dev_page() -> None:
    assert "docs/dev.md" not in PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("command", _commands())
def test_every_command_exists(command: str) -> None:
    words = shlex.split(command.replace("…", "x"))
    tool = words[0]
    if tool == "make":
        missing = set(words[1:]) - _make_targets()
        assert not missing, f"no Make target {sorted(missing)}"
    elif words[:2] == ["docker", "compose"]:
        assert (ROOT / "compose.yaml").exists()
        for index, word in enumerate(words):
            if word == "--profile":
                assert words[index + 1] in _compose_profiles(), f"no compose profile `{words[index + 1]}`"
    elif tool in {"cp", "python3"}:
        assert (ROOT / words[1]).exists(), f"`{words[1]}` is not in the repository"
    elif tool == "choregos":
        path = " ".join(w for w in words[:3] if not w.startswith(("-", "<")) and w.isascii())
        known = _cli_commands() | {"choregos login"}
        assert any(path == c or path.startswith(c + " ") for c in known), f"no CLI command for `{command}`"
    elif tool in {"git", "cd"} or "=" in tool:
        return  # cloning, moving, or a line of `.env`
    else:
        pytest.fail(f"a command this test does not know how to check: `{command}`")
