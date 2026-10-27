# SPDX-License-Identifier: Apache-2.0
"""`choregos plugin`: validate, pack and index plugins (ADR 0093), offline — no API is called."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml
from choregos_core.plugins import PluginError, build_index, opened, pack
from rich.console import Console

plugin_app = typer.Typer(help="Plugins: validate, pack and index them (ADR 0093)")
console = Console()


def _refuse(exc: PluginError) -> None:
    console.print("[red]✗[/red] invalid plugin:")
    for problem in exc.problems:
        console.print(f"  - {problem}", markup=False, highlight=False, soft_wrap=True)
    raise typer.Exit(1)


@plugin_app.command("validate")
def validate(
    path: Annotated[Path, typer.Argument(exists=True, help="A plugin directory or a packed .tar.gz.")],
) -> None:
    """Check a plugin and everything it ships; print its name, version and content digest."""
    try:
        with opened(path) as plugin:
            console.print(
                f"[green]✓[/green] {plugin.name} {plugin.version} {plugin.digest}",
                highlight=False,
                soft_wrap=True,
            )
    except PluginError as exc:
        _refuse(exc)


@plugin_app.command("pack")
def pack_(
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="The plugin directory.")],
    output: Annotated[Path, typer.Option("-o", "--output", help="The archive to write (.tar.gz).")],
) -> None:
    """Validate, then write a reproducible archive: sorted entries, time 0, owner 0. The same files
    always give the same bytes, and the printed digest is the one an index records."""
    try:
        plugin = pack(directory, output)
    except PluginError as exc:
        _refuse(exc)
        return
    console.print(
        f"[green]✓[/green] {output} {plugin.name} {plugin.version} {plugin.digest}",
        highlight=False,
        soft_wrap=True,
    )


@plugin_app.command("index")
def index(
    directory: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="A directory whose subdirectories are plugins."),
    ],
    output: Annotated[Path, typer.Option("-o", "--output", help="The index to write (index.yaml).")],
    oci_repository: Annotated[
        str | None,
        typer.Option("--oci-repository", help="Plugins are published as <repository>/<name>:<version>."),
    ] = None,
    git_url: Annotated[
        str | None, typer.Option("--git-url", help="Plugins are read from this repository.")
    ] = None,
    git_commit: Annotated[
        str | None, typer.Option("--git-commit", help="The commit (40 hex characters) they are read at.")
    ] = None,
    git_path: Annotated[
        str, typer.Option("--git-path", help="Where DIRECTORY sits in the repository, e.g. plugins.")
    ] = "",
) -> None:
    """Write a source index (plugin-index.schema.json) for every plugin under DIRECTORY, located
    either in an OCI repository or in a git commit."""
    try:
        doc = build_index(
            directory,
            oci_repository=oci_repository,
            git_url=git_url,
            git_commit=git_commit,
            git_path=git_path,
        )
    except PluginError as exc:
        _refuse(exc)
        return
    output.write_text(yaml.safe_dump(doc, sort_keys=True, allow_unicode=True), encoding="utf-8")
    console.print(
        f"[green]✓[/green] {output}: {len(doc['plugins'])} plugin(s)", highlight=False, soft_wrap=True
    )
