"""Le plugin Claude Code de Choregos : installable depuis le dépôt, sans secret, à la version du dépôt.

`/plugin marketplace add VargaFoundation/choregos` lit `.claude-plugin/marketplace.json` à la racine,
puis `/plugin install choregos@choregos` installe `integrations/claude-code` : sa porte MCP (ADR 0030)
et sa skill. Le jeton de l'utilisateur ne vit jamais dans le dépôt : c'est une valeur sensible de sa
configuration, gardée par Claude Code dans le coffre de sa machine.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = RACINE / "integrations" / "claude-code"


def _json(chemin: pathlib.Path) -> Any:
    return json.loads(chemin.read_text(encoding="utf-8"))


def test_le_marketplace_pointe_le_plugin() -> None:
    marche = _json(RACINE / ".claude-plugin" / "marketplace.json")
    assert marche["name"] == "choregos"
    (entree,) = marche["plugins"]
    assert entree["name"] == "choregos"
    assert (RACINE / entree["source"]).resolve() == PLUGIN.resolve()


def test_le_plugin_a_la_version_du_depot() -> None:
    version = re.search(r'^version = "([^"]+)"', (RACINE / "packages/core/pyproject.toml").read_text(), re.M)
    assert version
    assert _json(PLUGIN / ".claude-plugin" / "plugin.json")["version"] == version.group(1)


def test_le_jeton_est_une_valeur_sensible_et_le_mcp_ne_reference_que_des_valeurs_declarees() -> None:
    manifeste = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    options = manifeste["userConfig"]
    assert options["token"]["sensitive"] is True
    mcp = (PLUGIN / ".mcp.json").read_text(encoding="utf-8")
    references = set(re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", mcp))
    assert references == {"mcp_url", "token"}
    assert references <= set(options)


def test_aucun_secret_litteral() -> None:
    for fichier in [*PLUGIN.rglob("*"), RACINE / ".claude-plugin" / "marketplace.json"]:
        if fichier.is_file():
            assert "chg_" not in fichier.read_text(encoding="utf-8"), fichier


def test_la_skill_a_un_nom_et_une_description() -> None:
    texte = (PLUGIN / "skills" / "choregos" / "SKILL.md").read_text(encoding="utf-8")
    entete = re.match(r"^---\n(.*?)\n---\n", texte, re.S)
    assert entete
    champs = dict(ligne.split(": ", 1) for ligne in entete.group(1).splitlines())
    assert champs["name"] == "choregos"
    assert len(champs["description"]) > 50
    assert "decision_url" in texte, "la skill dit la règle : jamais de décision par MCP"


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code absent")
def test_claude_code_valide_le_plugin() -> None:
    for cible in (RACINE, PLUGIN):
        commande = ["claude", "plugin", "validate", "--strict", str(cible)]
        rendu = subprocess.run(commande, capture_output=True, text=True, check=False)
        assert rendu.returncode == 0, rendu.stdout + rendu.stderr
