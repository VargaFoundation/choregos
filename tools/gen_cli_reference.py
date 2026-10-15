#!/usr/bin/env python3
"""Generates `docs/cli.md` from the CLI itself: a reference that cannot lie.

    uv run python tools/gen_cli_reference.py            # writes docs/cli.md
    uv run python tools/gen_cli_reference.py --check    # fails if docs/cli.md diverges

There was no CLI reference, in any language (state of play of 2026-09-24): `usage.md`
showed five of its commands, `dev.md` six others, and nothing mentioned
`~/.config/choregos/config.json`. This file is produced by Typer from the real commands,
with their options and their help — and a test compares it with the repository.
"""

from __future__ import annotations

import os
import pathlib
import sys

# With rich, Typer PRINTS the help on a console and `get_help` returns an empty string.
# The variable is read when `typer.core` is imported: setting it before gives click's text help.
os.environ["TYPER_USE_RICH"] = "0"
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "cli.md"

HEADER = """# CLI reference

Generated from the CLI itself by `tools/gen_cli_reference.py` — do not edit by hand
(`make docs-cli` regenerates it, a test checks it is current).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` overrides the
path): `api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`,
`CHOREGOS_TOKEN` and `CHOREGOS_ORG` are read when no profile exists.

"""


def _render(command: Any, path: list[str], output: list[str]) -> None:
    """Typer bundles its own click (`typer._click`): a group is recognised by its `commands`."""
    name = " ".join(path)
    subcommands = getattr(command, "commands", None)
    if subcommands is not None:
        if len(path) > 1:
            output.append(f"## `{name}`\n")
            if command.help:
                output.append(command.help.strip() + "\n")
        for sub_name in sorted(subcommands):
            _render(subcommands[sub_name], [*path, sub_name], output)
        return
    context = command.make_context(name, [], resilient_parsing=True)
    output.append(f"### `{name}`\n")
    output.append("```text\n" + command.get_help(context).strip() + "\n```\n")


def generate() -> str:
    import typer.core
    from choregos_cli.main import app

    root = typer.main.get_command(app)
    output: list[str] = [HEADER]
    _render(root, ["choregos"], output)
    return "\n".join(output).rstrip() + "\n"


def main() -> int:
    content = generate()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != content:
            print(f"{TARGET.relative_to(ROOT)} is out of date: run `make docs-cli`", file=sys.stderr)
            return 1
        print("docs/cli.md is up to date")
        return 0
    TARGET.write_text(content, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
