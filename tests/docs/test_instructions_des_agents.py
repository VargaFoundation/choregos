"""`AGENTS.md` dit-il encore quelque chose, et dit-il vrai ?

Le 2026-09-24, la bascule de la documentation en anglais a remplacé le contenu d'`AGENTS.md`
par `@AGENTS.md` — un import de lui-même, onze octets. `CLAUDE.md` étant un lien symbolique
vers ce fichier, les instructions du dépôt étaient vides des deux côtés, et rien ne l'a vu :
ni la CI, ni le lint, ni un test. Le README continuait de dire « lis AGENTS.md ».

Deux propriétés, et la seconde est la vraie :

  * le fichier a de la substance et ne se référence pas lui-même ;
  * **chaque `make <cible>` qu'il nomme existe vraiment dans le Makefile.** L'état des lieux
    du 2026-09-24 reprochait déjà à ce fichier des commandes fausses : une instruction qui
    envoie un agent sur une cible inexistante coûte plus qu'une absence d'instruction.
"""

from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]
AGENTS = RACINE / "AGENTS.md"
MAKEFILE = RACINE / "Makefile"

#: Sections sans lesquelles le fichier ne rend plus le service qu'on attend de lui.
SECTIONS = ("## Contexte", "## Commandes", "## Conventions", "## Definition of done")


def _texte() -> str:
    return AGENTS.read_text(encoding="utf-8")


def test_les_instructions_ne_se_referencent_pas_elles_memes() -> None:
    texte = _texte().strip()
    assert texte != "@AGENTS.md", (
        "AGENTS.md est redevenu un import de lui-même. `CLAUDE.md` pointe dessus : les "
        "instructions du dépôt sont vides des deux côtés."
    )
    assert len(texte) > 1000, f"AGENTS.md ne fait que {len(texte)} octets — il ne dit plus rien"


def test_les_sections_attendues_sont_la() -> None:
    texte = _texte()
    manquantes = [titre for titre in SECTIONS if titre not in texte]
    assert not manquantes, f"sections absentes d'AGENTS.md : {manquantes}"


def test_claude_md_et_agents_md_sont_le_meme_fichier() -> None:
    """Deux fichiers d'instructions qui divergent, c'est un fichier d'instructions faux."""
    claude = RACINE / "CLAUDE.md"
    assert claude.exists(), "CLAUDE.md a disparu"
    assert claude.resolve() == AGENTS.resolve(), (
        "CLAUDE.md n'est plus le même fichier qu'AGENTS.md : ils vont diverger"
    )


def test_chaque_cible_make_citee_existe() -> None:
    cibles = {
        ligne.split(":", 1)[0]
        for ligne in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[a-z][a-z0-9-]*:", ligne)
    }
    citees = set(re.findall(r"`make ([a-z][a-z0-9-]*)`", _texte()))
    assert citees, "AGENTS.md ne cite plus aucune commande `make`"
    inconnues = sorted(citees - cibles)
    assert not inconnues, (
        f"AGENTS.md envoie sur des cibles qui n'existent pas : {inconnues}. "
        "Une instruction fausse coûte plus cher qu'une instruction absente."
    )
