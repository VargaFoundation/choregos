"""Les erreurs de l'API se lisent en anglais (ADR 0039, S21-12).

Ce qu'un client HTTP reçoit quand l'API refuse — le `title` et le `detail` d'un problème RFC 9457,
les `msg` de sa liste `errors`, le refus de la porte MCP — est balayé dans l'arbre syntaxique de
`choregos_api` et des routes que l'ontologie y monte : chaque chaîne littérale, et la partie
constante de chaque f-string, passée à un constructeur d'erreur (`not_found`, `forbidden`,
`ApiError`…, et les refus que l'API rend en réponse : `SkillRefusee`, `ArgumentsRefuses`, `Refus`
de la porte…) ou rangée sous une clé `title`, `detail`, `msg` ou `error` d'un dictionnaire
littéral. Un extrait de code (entre accents graves) n'est pas de la prose : un nom d'état, de champ
ou de portée y reste ce qu'il est.

Les journaux (`logger.info(...)`) ne sont pas lus : ils parlent aux exploitants, côté code, et
restent en français comme les commentaires.

Le détecteur est celui de `tests/langue/test_anglais.py`, chargé par son chemin : sous
`--import-mode=importlib`, un module de test n'en importe pas un autre, et une seconde copie
divergerait. Il ne connaissait pas le vocabulaire des refus de l'API — « Projet introuvable »,
« jeton de run manquant », « signature HMAC invalide » passaient sans accent ni mot outil : le
lexique ci-dessous le complète, pour ce balayage.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
from collections.abc import Callable, Iterator
from types import ModuleType

RACINE = pathlib.Path(__file__).resolve().parents[3]
#: L'API, et les routes de l'ontologie qu'elle sert (`/projects/{id}/ontology`, `/proposals`…).
SOURCES = (RACINE / "apps/api/src/choregos_api", RACINE / "packages/ontology/src/choregos_ontology/service")


def _garde_de_langue() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_garde_de_langue", RACINE / "tests/langue/test_anglais.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GARDE = _garde_de_langue()
ressemble_a_du_francais: Callable[[str], bool] = _GARDE.ressemble_a_du_francais
_prose: Callable[[str], str] = _GARDE._prose

#: Les mots des refus de l'API que le détecteur commun laissait passer, et l'élision (« l'agent »,
#: « n'a », « d'agent ») qu'aucune contraction anglaise n'écrit après un mot d'une seule lettre.
_LEXIQUE_DES_ERREURS = re.compile(
    r"\b(introuvable|existe|inconnue?s?|invalide|manquante?|manque|jetons?|aucune?|plafond|atteint|"
    r"fichiers?|gabarits?|exige|requise?|interdite?|projets?|connecteurs?|outils?|appartenance|droits|"
    r"traitable|authentification|erreur|manifeste|illisible|orphelin|ambigu|resserrer|seule?|doit|peut|"
    r"injoignable|dossier|rejet|pourquoi|fourni|toute|dans|sur|de|du|trop|octets|mio|kio)\b"
    r"|\b(qu|[cdjlmnst])'\w",
    re.IGNORECASE,
)


#: Les constructeurs d'erreur, et les refus que l'API rend tels quels à son client.
CONSTRUCTEURS = {
    "not_found",
    "forbidden",
    "unauthorized",
    "conflict",
    "unprocessable",
    "upstream",
    "ApiError",
    "HTTPException",
    # La porte MCP : réponses HTTP et erreurs JSON-RPC.
    "Refus",
    "JetonRefuse",
    "OutilRefuse",
    "_refus_http",
    "_faute",
    "erreur",
    # Les refus convertis en 400 / 422 / 401 / 403 / 409 par une route.
    "SkillRefusee",
    "ArgumentsRefuses",
    "GesteRefuse",
    "ToolRefusal",
    "Refusal",
    # Le refus rendu au run, dont la console montre la raison.
    "AgentIndisponible",
}
#: Les clés d'un dictionnaire littéral dont la valeur part dans une réponse d'erreur.
CLES_LUES = {"title", "detail", "msg", "error"}

#: Ce qui reste en français dans une erreur de l'API, et pourquoi : `(fichier, extrait)`. Cette liste
#: ne peut que raccourcir — `test_la_liste_des_restes_ne_garde_que_ce_qui_existe` refuse une entrée
#: qui ne correspond plus à rien.
RESTE_EN_FRANCAIS: set[tuple[str, str]] = set()


def _nom(appel: ast.Call) -> str:
    if isinstance(appel.func, ast.Name):
        return appel.func.id
    if isinstance(appel.func, ast.Attribute):
        return appel.func.attr
    return ""


def _chaines(noeud: ast.AST) -> Iterator[tuple[int, str]]:
    """Les chaînes sous un nœud ; une f-string se lit sans ses trous, d'un seul tenant."""
    dans_une_fstring: set[int] = set()
    for sous in ast.walk(noeud):
        if isinstance(sous, ast.JoinedStr):
            dans_une_fstring.update(id(v) for v in sous.values)
            yield (
                sous.lineno,
                "".join(
                    v.value for v in sous.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ),
            )
    for sous in ast.walk(noeud):
        if (
            isinstance(sous, ast.Constant)
            and isinstance(sous.value, str)
            and id(sous) not in dans_une_fstring
        ):
            yield sous.lineno, sous.value


def chaines_d_erreur(source: str) -> Iterator[tuple[int, str]]:
    """Ce qu'un module de l'API peut mettre dans une réponse d'erreur."""
    for noeud in ast.walk(ast.parse(source)):
        if isinstance(noeud, ast.Call) and _nom(noeud) in CONSTRUCTEURS:
            for argument in [*noeud.args, *(mot.value for mot in noeud.keywords)]:
                yield from _chaines(argument)
        elif isinstance(noeud, ast.Dict):
            for cle, valeur in zip(noeud.keys, noeud.values, strict=True):
                if isinstance(cle, ast.Constant) and cle.value in CLES_LUES:
                    yield from _chaines(valeur)


def francais(texte: str) -> bool:
    prose = _prose(texte)
    return ressemble_a_du_francais(prose) or bool(_LEXIQUE_DES_ERREURS.search(prose))


def _modules() -> Iterator[tuple[str, str]]:
    for chemin in sorted(c for dossier in SOURCES for c in dossier.rglob("*.py")):
        yield str(chemin.relative_to(RACINE)), chemin.read_text(encoding="utf-8")


def _en_francais() -> list[tuple[str, int, str]]:
    return [
        (relatif, ligne, texte)
        for relatif, source in _modules()
        for ligne, texte in sorted(set(chaines_d_erreur(source)))
        if francais(texte)
    ]


def test_le_detecteur_reconnait_les_refus_de_l_api() -> None:
    """Les refus d'avant S21-12 qu'aucun accent ni mot outil ne trahissait, et leur traduction."""
    for avant in (
        "Projet introuvable",
        "jeton de run manquant",
        "signature HMAC invalide",
        "manifeste de template invalide",
        "`token_id` manque",
        "Plafond de findings atteint",
        "authentification requise",
    ):
        assert francais(avant), avant
    for apres in (
        "Project not found",
        "Project `acme/billing` does not exist",
        "missing run token",
        "invalid HMAC signature",
        "insufficient rights: `workflow:write` needs a higher role on acme/billing",
        "the secret must be a reference (`env:NAME`), not a value in clear",
        "Organisation `acme` does not exist",
        "the task asks you to attest: “the badge was handed over”",
    ):
        assert not francais(apres), apres


def test_le_balayage_voit_les_erreurs_de_l_api() -> None:
    """Un balayage qui ne verrait plus rien serait vert pour rien : il voit les constructeurs."""
    vues = {texte for _, source in _modules() for _, texte in chaines_d_erreur(source)}
    assert {"Forbidden", "Not authenticated", "State conflict", "Unprocessable entity"} <= vues
    assert " not found" in vues and "invalid shared secret" in vues
    assert len(vues) > 200


def test_aucune_erreur_de_l_api_n_est_en_francais() -> None:
    fautes = [
        f"{relatif}:{ligne}: {texte[:90]!r}"
        for relatif, ligne, texte in _en_francais()
        if not any(relatif == fichier and extrait in texte for fichier, extrait in RESTE_EN_FRANCAIS)
    ]
    assert not fautes, "du français dans ce que l'API répond :\n  " + "\n  ".join(fautes)


def test_la_liste_des_restes_ne_garde_que_ce_qui_existe() -> None:
    """Une entrée traduite depuis sort de la liste : elle ne peut que raccourcir."""
    restants = _en_francais()
    obsoletes = [
        f"{fichier}: {extrait!r}"
        for fichier, extrait in sorted(RESTE_EN_FRANCAIS)
        if not any(relatif == fichier and extrait in texte for relatif, _, texte in restants)
    ]
    assert not obsoletes, "déjà traduit, à retirer de RESTE_EN_FRANCAIS :\n  " + "\n  ".join(obsoletes)
