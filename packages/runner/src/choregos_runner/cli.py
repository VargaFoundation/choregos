"""CLI du runner : `choregos-runner run`, et quelques commandes de diagnostic."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from .backends import backend_version, known_backends
from .config import get_settings, reset_settings_cache
from .exits import Exit
from .runner import Runner

app = typer.Typer(
    add_completion=False,
    help="Runner Choregos : exécute une étape d'agent et publie son StageResult.",
)


@app.command()
def run(
    run_id: Annotated[str, typer.Option("--run-id", envvar="CHOREGOS_RUN_ID")] = "",
    api_url: Annotated[str, typer.Option("--api-url", envvar="CHOREGOS_API_URL")] = "",
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    write_result_url: Annotated[Path | None, typer.Option("--write-result-url")] = None,
    write_status: Annotated[Path | None, typer.Option("--write-status")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="ne pousse pas la branche")] = False,
) -> None:
    """Exécute l'étape désignée par `--run-id` et sort avec le code documenté (§2.2)."""
    reset_settings_cache()
    settings = get_settings()
    if run_id:
        settings.run_id = run_id
    if api_url:
        settings.api_url = api_url
    if workspace is not None:
        settings.workspace = workspace
    settings.dry_run = settings.dry_run or dry_run

    outcome = asyncio.run(Runner(settings).run())
    status = str(outcome.result.status) if outcome.result else "failed"
    typer.echo(
        json.dumps(
            {
                "run_id": settings.run_id,
                "exit": int(outcome.exit_code),
                "explanation": outcome.exit_code.explanation,
                "status": status,
                "summary": outcome.result.summary if outcome.result else outcome.detail,
            },
            ensure_ascii=False,
        )
    )
    # Résultats Tekton : l'orchestrateur les lit si le runner n'a pas pu poster.
    if write_status is not None:
        write_status.write_text(status, encoding="utf-8")
    if write_result_url is not None and outcome.result is not None:
        url = outcome.result.artifacts.transcript_url or ""
        write_result_url.write_text(url.replace("transcript.jsonl", "result.json"), encoding="utf-8")
    raise typer.Exit(int(outcome.exit_code))


@app.command()
def backends() -> None:
    """Liste les backends ACP connus et les versions épinglées dans l'image."""
    for name in known_backends():
        typer.echo(f"{name:<14} {backend_version(name) or '—'}")


@app.command()
def validate(path: Annotated[Path, typer.Argument()] = Path(".choregos/result.json")) -> None:
    """Valide un `result.json` contre le contrat, avec des erreurs lisibles."""
    from .result import load_result

    load = load_result(path)
    if load.ok and load.result is not None:
        typer.echo(f"valide — status={load.result.status} : {load.result.summary}")
        raise typer.Exit(0)
    typer.echo(load.error or "invalide", err=True)
    raise typer.Exit(int(Exit.INVALID_RESULT))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
