# SPDX-License-Identifier: Apache-2.0
"""Jouer les migrations — celles du cœur ET celles que déclarent les greffons.

    python -m choregos_api.migrer              # upgrade heads
    python -m choregos_api.migrer downgrade -1

C'est la commande du Job de migration du chart. Elle remplace `alembic -c alembic.ini upgrade
head`, qui avait deux défauts :

1. **Elle ne marchait que depuis `/app/apps/api`** : `script_location = migrations` était relatif,
   et les migrations n'étaient pas dans la roue `choregos-api` (59 fichiers, aucun
   `migrations/`). Hors de l'image, personne ne pouvait migrer une base avec le cœur publié. Elles
   vivent désormais DANS le paquet (`choregos_api/migrations`), et cette commande les trouve où
   qu'elle soit lancée.
2. **Un greffon ne pouvait pas apporter de schéma.** Il déclare maintenant un emplacement de
   versions dans le groupe de points d'entrée `choregos.migrations` ; ses révisions forment une
   BRANCHE Alembic (`branch_labels`, `depends_on` une révision du cœur) dans la même table
   `alembic_version`, et `upgrade heads` joue le cœur puis elle. Un greffon crée SES tables ; il ne
   modifie pas celles du cœur — deux propriétaires pour un même schéma, c'est la migration du cœur
   suivante qui casse.

Un emplacement déclaré qui n'existe pas arrête la commande : un schéma de greffon silencieusement
absent donnerait une plateforme qui démarre et échoue à la première requête qui touche ses tables.
"""

from __future__ import annotations

import os
import pathlib
import sys
from importlib import metadata

from alembic import command
from alembic.config import Config

#: Le groupe de points d'entrée où un greffon déclare ses migrations. La valeur désignée est un
#: chemin (`str` ou `pathlib.Path`) vers un répertoire de révisions Alembic.
GROUPE_DE_MIGRATIONS = "choregos.migrations"

MIGRATIONS = pathlib.Path(__file__).resolve().parent / "migrations"


def emplacements_des_greffons(groupe: str = GROUPE_DE_MIGRATIONS) -> list[pathlib.Path]:
    emplacements: list[pathlib.Path] = []
    for point in metadata.entry_points(group=groupe):
        valeur = point.load()
        if not isinstance(valeur, str | os.PathLike):
            raise TypeError(
                f"migrations du greffon « {point.name} » ({point.value}) : un chemin est attendu, "
                f"pas {type(valeur).__name__}"
            )
        chemin = pathlib.Path(valeur).resolve()
        if not chemin.is_dir():
            raise RuntimeError(
                f"migrations du greffon « {point.name} » : {chemin} n'existe pas. Elles sont "
                "déclarées, donc elles doivent être jouées ; la base serait sans leurs tables."
            )
        emplacements.append(chemin)
    return emplacements


def configuration(*, avec_les_greffons: bool = True) -> Config:
    """La configuration Alembic, construite ici plutôt que lue d'un fichier relatif au cwd."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("path_separator", "os")
    emplacements = [MIGRATIONS / "versions"]
    if avec_les_greffons:
        emplacements += emplacements_des_greffons()
    config.set_main_option("version_locations", os.pathsep.join(str(e) for e in emplacements))
    return config


def main(arguments: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    action = arguments[0] if arguments else "upgrade"
    cible = arguments[1] if len(arguments) > 1 else ("heads" if action == "upgrade" else "-1")
    config = configuration()
    if action == "upgrade":
        command.upgrade(config, cible)
    elif action == "downgrade":
        command.downgrade(config, cible)
    elif action == "heads":
        command.heads(config, verbose=True)
    elif action == "current":
        command.current(config, verbose=True)
    else:
        raise SystemExit(f"action inconnue : {action!r} (upgrade, downgrade, heads, current)")


if __name__ == "__main__":
    main()
