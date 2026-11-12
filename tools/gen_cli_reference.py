#!/usr/bin/env python3
"""Generates `docs/cli.md` and the public page `docs/site/cli/reference.md` from the CLI itself.

    uv run python tools/gen_cli_reference.py            # writes both
    uv run python tools/gen_cli_reference.py --check    # fails if either diverges

The public page (S62-09b) is the same reference under the documentation site's front matter: one
generator, so the two never say different things.

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

from choregos_core.publishing import PUBLISHING, SIGNING_ISSUER

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "cli.md"
SITE_TARGET = ROOT / "docs" / "site" / "cli" / "reference.md"

HEADER = """# CLI reference

Generated from the CLI itself by `tools/gen_cli_reference.py` — do not edit by hand
(`make docs-cli` regenerates it, a test checks it is current).

## Installing the CLI

`choregos` ships as one native, self-contained file per platform: nothing to install first, no
Python, no SDK, no runtime. Each release attaches them to its GitHub release, under names that
never change, and the API image carries the same files in `/opt/choregos/cli/`:

| Platform | File |
|:--|:--|
| Linux x86_64 (glibc 2.28 or later: RHEL 8, Debian 10, Ubuntu 18.10 on) | `choregos-linux-x86_64` |
| Linux arm64 (same) | `choregos-linux-arm64` |
| macOS, Apple silicon | `choregos-macos-arm64` |
| macOS, Intel | `choregos-macos-x86_64` |
| Windows x86_64 | `choregos-windows-x86_64.exe` |

Beside them, `SHA256SUMS`, `manifest.json` (the version, and each file's platform, digest and
size) and a cosign bundle per file (`<file>.sigstore.json`). Download one, check it, put it on the
PATH:

```sh
curl -fsSLO __RELEASES__/download/v<version>/choregos-linux-x86_64
curl -fsSLO __RELEASES__/download/v<version>/SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS          # macOS: shasum -a 256 --ignore-missing -c SHA256SUMS
install -m 0755 choregos-linux-x86_64 ~/.local/bin/choregos
choregos --version
```

`cosign verify-blob --bundle SHA256SUMS.sigstore.json --certificate-identity
__RELEASE_IDENTITY__
--certificate-oidc-issuer __ISSUER__ SHA256SUMS` proves the sums
come from the release workflow. The Windows and macOS files are not yet signed by a platform
vendor (`docs/plan/BLOCKERS.md`): Windows SmartScreen warns, and a macOS browser download needs
`xattr -d com.apple.quarantine choregos-macos-arm64` once. The first call unpacks the binary into
the user's cache directory (`~/.cache/choregos`, `~/Library/Caches/choregos`,
`%LOCALAPPDATA%\\choregos`); later calls start from there.

From a checkout, `make cli-binary` builds the binary of the machine it runs on into `dist/cli/`
(Nuitka; on Linux in the `manylinux_2_28` image, so docker is needed) and `make cli-binary-smoke`
runs the release's smoke test on it. The Python package (`packages/cli`, `uv run choregos`)
remains the way to work on the CLI itself.

## Profile

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` overrides the
path): `api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`,
`CHOREGOS_TOKEN` and `CHOREGOS_ORG` are read when no profile exists.

## Install from your instance

Every Choregos instance serves the CLI in the exact version it runs, through the console's host:
no Python, no access to GitHub needed. The console shows the command for your system under
Integrations › CLI.

```sh
curl -fsSL https://<your-instance>/cli/install.sh | sh        # Linux, macOS: ~/.local/bin/choregos
irm https://<your-instance>/cli/install.ps1 | iex             # Windows: %LOCALAPPDATA%\\Programs\\choregos
```

The scripts check the binary's sha256 against the instance's manifest
(`/downloads/cli/manifest.json`) and never use sudo; `sh -s -- --prefix DIR` installs into `DIR/bin`.
`choregos self update` later replaces the binary with the instance's current one. When the CLI
talks to an instance that runs another version, it says so in one line on stderr, at most once a
day (`CHOREGOS_NO_VERSION_CHECK=1` silences it).

"""
# The release pages and the release workflow's signing identity come from the publishing identity
# (ADR 0156): the generated page follows it.
HEADER = (
    HEADER.replace("__RELEASES__", PUBLISHING.releases)
    .replace("__RELEASE_IDENTITY__", PUBLISHING.signing_identity("refs/tags/v<version>"))
    .replace("__ISSUER__", SIGNING_ISSUER)
)

SITE_HEADER = """---
title: CLI reference
description: Every choregos command and option, generated from the CLI itself.
sidebar_position: 2
---

# CLI reference

This page is generated from the CLI itself, and the repository's tests fail when it differs from the
commands. `choregos <command> --help` prints the same text for the version you installed
([Install the CLI](install.md)).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` names another file):
`api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`, `CHOREGOS_TOKEN` and
`CHOREGOS_ORG` are read when no profile exists.

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
            if getattr(subcommands[sub_name], "hidden", False):
                continue  # not for people (`binary-info` is the binaries' smoke test's)
            _render(subcommands[sub_name], [*path, sub_name], output)
        return
    context = command.make_context(name, [], resilient_parsing=True)
    output.append(f"### `{name}`\n")
    output.append("```text\n" + command.get_help(context).strip() + "\n```\n")


def generate(header: str = HEADER) -> str:
    import typer.core
    from choregos_cli.main import app

    root = typer.main.get_command(app)
    output: list[str] = [header]
    _render(root, ["choregos"], output)
    return "\n".join(output).rstrip() + "\n"


def targets() -> dict[pathlib.Path, str]:
    """Each generated file and what it must hold."""
    return {TARGET: generate(), SITE_TARGET: generate(SITE_HEADER)}


def main() -> int:
    if "--check" in sys.argv:
        stale = [
            path
            for path, content in targets().items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        for path in stale:
            print(f"{path.relative_to(ROOT)} is out of date: run `make docs-cli`", file=sys.stderr)
        if stale:
            return 1
        print("docs/cli.md and docs/site/cli/reference.md are up to date")
        return 0
    for path, content in targets().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
