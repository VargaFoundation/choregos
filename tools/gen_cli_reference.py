#!/usr/bin/env python3
"""Génère `docs/cli.md` depuis la CLI elle-même : une référence qui ne peut pas mentir.

    uv run python tools/gen_cli_reference.py            # écrit docs/cli.md
    uv run python tools/gen_cli_reference.py --check    # échoue si docs/cli.md diverge

Il n'existait aucune référence de la CLI, dans aucune langue (état des lieux du
2026-09-24) : `usage.md` en montrait cinq commandes, `dev.md` six autres, et rien ne disait
`~/.config/choregos/config.json`. Ce fichier est produit par Typer à partir des commandes
réelles, avec leurs options et leurs aides — et un test le compare au dépôt.
"""

from __future__ import annotations

import os
import pathlib
import sys

# Avec rich, Typer IMPRIME l'aide sur une console et `get_help` rend une chaîne vide.
# La variable se lit à l'import de `typer.core` : la poser avant, c'est l'aide texte de click.
os.environ["TYPER_USE_RICH"] = "0"
from typing import Any

RACINE = pathlib.Path(__file__).resolve().parents[1]
CIBLE = RACINE / "docs" / "cli.md"

EN_TETE = """# CLI reference

Generated from the CLI itself by `tools/gen_cli_reference.py` — do not edit by hand
(`make docs-cli` regenerates it, a test checks it is current).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` overrides the
path): `api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`,
`CHOREGOS_TOKEN` and `CHOREGOS_ORG` are read when no profile exists. Help strings are in
French, like the rest of the code.

"""


def _rendre(commande: Any, chemin: list[str], sortie: list[str]) -> None:
    """Typer embarque son propre click (`typer._click`) : on reconnaît un groupe à ses `commands`."""
    nom = " ".join(chemin)
    sous_commandes = getattr(commande, "commands", None)
    if sous_commandes is not None:
        if len(chemin) > 1:
            sortie.append(f"## `{nom}`\n")
            if commande.help:
                sortie.append(commande.help.strip() + "\n")
        for sous_nom in sorted(sous_commandes):
            _rendre(sous_commandes[sous_nom], [*chemin, sous_nom], sortie)
        return
    contexte = commande.make_context(nom, [], resilient_parsing=True)
    sortie.append(f"### `{nom}`\n")
    sortie.append("```text\n" + commande.get_help(contexte).strip() + "\n```\n")


def generer() -> str:
    import typer.core
    from choregos_cli.main import app

    racine = typer.main.get_command(app)
    sortie: list[str] = [EN_TETE]
    _rendre(racine, ["choregos"], sortie)
    return "\n".join(sortie).rstrip() + "\n"


def main() -> int:
    contenu = generer()
    if "--check" in sys.argv:
        if not CIBLE.exists() or CIBLE.read_text(encoding="utf-8") != contenu:
            print(f"{CIBLE.relative_to(RACINE)} n'est pas à jour : lancer `make docs-cli`", file=sys.stderr)
            return 1
        print("docs/cli.md à jour")
        return 0
    CIBLE.write_text(contenu, encoding="utf-8")
    print(f"écrit {CIBLE.relative_to(RACINE)} ({len(contenu.splitlines())} lignes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
