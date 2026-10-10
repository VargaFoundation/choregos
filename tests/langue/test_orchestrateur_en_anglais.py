"""Ce que l'orchestrateur écrit pour quelqu'un est en anglais (ADR 0039, S21-13).

Le commentaire de suivi du ticket, les notifications, les demandes humaines, les raisons d'un état,
les étapes du provisioning, les issues de findings, la sortie de `make demo` : tout cela se lit dans
le tracker, Slack ou la console. Le code, les commentaires, les docstrings et les journaux restent en
français. La liste `RESTE_EN_FRANCAIS` ne peut que raccourcir : une entrée qui ne correspond plus à
rien fait rougir la suite.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
ORCHESTRATEUR = RACINE / "apps/orchestrator/src/choregos_orchestrator"
JOURNAUX = {"debug", "info", "warning", "error", "exception", "critical", "warn"}

#: (fichier relatif, début du littéral) — chacune avec sa raison.
RESTE_EN_FRANCAIS = {
    # Le banc d'évaluation des agents : un outil de la plateforme, lu par ses développeurs.
    ("activities/evals.py", "fichier créé alors"),
    ("activities/evals.py", "fichier modifié hors"),
    ("activities/evals.py", "dépôt jouet absent"),
    ("activities/evals.py", "aucune modification scriptée"),
    ("activities/evals.py", "présent"),
    ("activities/evals.py", "délai dépassé ("),
    # Une expression régulière, pas un texte.
    ("activities/findings.py", "[a-zà-ÿ0-9_]+"),
    # Les anciens noms des champs du board (`Coût (€)`, `Taille`, `Risque`) ne sont plus écrits par
    # l'orchestrateur depuis S22-21 : ils vivent hors de lui, dans
    # `choregos_adapters/tracker/champs.py` (ANCIENS_NOMS), le temps qu'un board d'avant soit renommé.
}


def _detecteur() -> object:
    spec = importlib.util.spec_from_file_location(
        "anglais", pathlib.Path(__file__).with_name("test_anglais.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ressemble_a_du_francais


def _litteraux_en_francais() -> set[tuple[str, str, int]]:
    francais = _detecteur()
    trouves: set[tuple[str, str, int]] = set()
    for chemin in sorted(ORCHESTRATEUR.rglob("*.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        a_ignorer: set[int] = set()
        for noeud in ast.walk(arbre):
            corps = getattr(noeud, "body", None)
            if (
                isinstance(noeud, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and corps
            ):
                premier = corps[0]
                if isinstance(premier, ast.Expr) and isinstance(premier.value, ast.Constant):
                    a_ignorer.add(id(premier.value))
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr in JOURNAUX
            ):
                a_ignorer.update(id(sous) for sous in ast.walk(noeud))
        for noeud in ast.walk(arbre):
            if (
                not (isinstance(noeud, ast.Constant) and isinstance(noeud.value, str))
                or id(noeud) in a_ignorer
            ):
                continue
            if len(noeud.value) > 3 and francais(noeud.value):  # type: ignore[operator]
                trouves.add((chemin.relative_to(ORCHESTRATEUR).as_posix(), noeud.value, noeud.lineno))
    return trouves


def _autorise(fichier: str, texte: str) -> bool:
    return any(fichier == f and texte.startswith(debut) for f, debut in RESTE_EN_FRANCAIS)


def test_l_orchestrateur_n_ecrit_pas_de_francais_a_ses_lecteurs() -> None:
    restes = sorted(
        f"{fichier}:{ligne}: {texte[:80]!r}"
        for fichier, texte, ligne in _litteraux_en_francais()
        if not _autorise(fichier, texte)
    )
    assert not restes, "\n".join(restes)


def test_la_liste_des_exceptions_ne_fait_que_raccourcir() -> None:
    trouves = {(fichier, texte) for fichier, texte, _ in _litteraux_en_francais()}
    perimees = [
        entree
        for entree in sorted(RESTE_EN_FRANCAIS)
        if not any(f == entree[0] and t.startswith(entree[1]) for f, t in trouves)
    ]
    assert not perimees, f"retirer de RESTE_EN_FRANCAIS : {perimees}"


def test_le_commentaire_de_suivi_et_une_demande_humaine_se_lisent_en_anglais() -> None:
    from choregos_orchestrator.markdown import StageLine, StatusComment, render_human_request

    ligne = StageLine(
        1, "Refining", "agent refine", "opencode", "standard", 12_400, 1_800, 0, 0.42, 7_380, "spec written"
    )
    rendu = StatusComment(
        workflow="dev-simple", workflow_version=1, size="S", risk="low", state_display="Implemented",
        budget_eur=9.2, spent_eur=0.42, estimate_eur=0.5, lines=[ligne],
    ).render()  # fmt: skip
    assert "### Choregos — progress" in rendu
    assert "**Size** S · **Risk** low · **State** Implemented" in rendu
    assert "| # | Step | Actor |" in rendu and "€0.42" in rendu and "2 h 3 min" in rendu
    assert "estimated €0.50" in rendu
    demande = render_human_request("approval", {}, "https://choregos.example/p/dev/items/1", "dev#1")
    assert "### Choregos — approval requested" in demande
    assert "Approve or refuse in the console: https://choregos.example/p/dev/items/1" in demande


#: Les champs du board d'avant S22-21 : des noms qu'un tracker porte encore, que rien n'écrit plus.
ANCIENS_NOMS_DU_BOARD = {"Coût (€)", "Taille", "Risque"}
TABLE_DE_COMPATIBILITE = "packages/adapters/src/choregos_adapters/tracker/champs.py"


def test_les_anciens_noms_du_board_ne_vivent_que_dans_la_table_de_compatibilite() -> None:
    """S22-21 (#290) : l'orchestrateur, les adaptateurs et les gabarits nomment `Cost (€)`, `Size`,
    `Risk`. Les noms français ne restent que dans `ANCIENS_NOMS`, pour renommer un board d'avant et y
    écrire tant qu'il n'est pas renommé — une version, puis la table disparaît."""
    ou: dict[str, set[str]] = {}
    for racine in (ORCHESTRATEUR, RACINE / "packages/adapters/src/choregos_adapters"):
        for chemin in sorted(racine.rglob("*.py")):
            for noeud in ast.walk(ast.parse(chemin.read_text(encoding="utf-8"))):
                if isinstance(noeud, ast.Constant) and noeud.value in ANCIENS_NOMS_DU_BOARD:
                    ou.setdefault(chemin.relative_to(RACINE).as_posix(), set()).add(noeud.value)
    for manifeste in sorted((RACINE / "templates").rglob("manifest.yaml")):
        texte = manifeste.read_text(encoding="utf-8")
        for nom in ANCIENS_NOMS_DU_BOARD:
            if nom in texte:
                ou.setdefault(manifeste.relative_to(RACINE).as_posix(), set()).add(nom)
    assert ou == {TABLE_DE_COMPATIBILITE: ANCIENS_NOMS_DU_BOARD}
