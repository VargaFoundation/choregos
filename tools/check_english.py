#!/usr/bin/env python3
"""Refuse French in new or modified lines, and keep the French that is left from growing (ADR 0046, 0098).

    uv run python tools/check_english.py                      # origin/main...HEAD
    uv run python tools/check_english.py --range A...B        # what CI runs on a pull request
    uv run python tools/check_english.py --no-commits         # lines only, not commit messages
    uv run python tools/check_english.py --all                # the whole tree against its baselines
    uv run python tools/check_english.py --all --report       # ... and the totals per area and pass
    uv run python tools/check_english.py --update-baseline --area core   # lower one area's counts

The default mode reads only the lines a change ADDS: the existing French code is renamed package by
package (S34-04, S45), and a guard that failed on every untouched line would be switched off on
day one. In those lines, only what is prose is read: comments, docstrings and string literals in
Python and TypeScript, JSX text, Markdown outside code blocks, YAML values and comments, Makefile
and shell comments. Identifiers are keys, not prose: a modified line that calls an existing French
function is not flagged; a sentence that names one should put it in backticks.

`--all` (ADR 0098) counts, for every tracked file, three things: the French prose lines, the
distinct French names the file declares (Python by its syntax tree, TypeScript by its declaration
keywords) and whether a component of its path is French. The counts live in
`tools/english_baseline/<area>.tsv`; a count can go down, never up, and a new file with French
fails. A line that must stay French carries `lang: fr-ok` in a comment, a shim carries
`lang: fr-ok-file` in its first five lines.

The prose detector extends the shipped-text one of ADR 0039 (`looks_like_shipped_french`): French-only
accents, a French lexicon, French contractions (`l'`, `d'`, `qu'`), French function words.
"""

from __future__ import annotations

import argparse
import ast
import io
import pathlib
import re
import shutil
import subprocess
import sys
import tokenize
import warnings
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RANGE = "origin/main...HEAD"
ESCAPE = "lang: fr-ok"
#: In one of a file's first five lines: every full-scan pass skips the file. For a compatibility
#: shim or a redirect stub, whose French is the point (ADR 0098 §3). `ESCAPE` is a prefix of it, so
#: a line written `lang: fr-ok-file` is also an escaped line.
FILE_ESCAPE = "lang: fr-ok-file"

#: Where prose lives. Root-level Markdown (AGENTS.md, README.md) and the Makefile are read too.
SCANNED_DIRS = (
    "packages/",
    "apps/",
    "tools/",
    "tests/",
    "docs/",
    ".github/",
    "charts/",
    "dev/",
    "demo/",
    "templates/",  # S39-11: a shipped template is read by every user
    "essai/",  # ADR 0098: the trials, French until S45 renames them
    "trials/",
    "infra/",
    "docker/",
    "examples/",
    "integrations/",
)
#: `.tpl` and `.txt`: a Helm chart's helpers and its NOTES, read line by line as YAML is. Since
#: ADR 0098: shell, TOML, INI and Dockerfiles (`#` comments), SQL (`--`), scripts and styles of the
#: web and Rust (`//` and `/* */`), HTML and Jinja (the text outside the markup). Never `.json`.
SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".md",
    ".yaml",
    ".yml",
    ".tpl",
    ".txt",
    ".sh",
    ".toml",
    ".ini",
    ".Dockerfile",
    ".sql",
    ".js",
    ".mjs",
    ".mts",
    ".css",
    ".rs",
    ".html",
    ".j2",
}
#: Files read by their whole name, wherever they sit under a scanned directory.
SCANNED_NAMES = {"Dockerfile", "justfile", "CODEOWNERS"}
#: Root files read besides the root Markdown and the Makefile.
SCANNED_ROOT_FILES = {"compose.yaml", "justfile", "pyproject.toml", "cliff.toml", "CODEOWNERS"}
#: Paths never read, and why. A line here is a decision, not an oversight.
EXCLUDED = (
    "docs/fr/",  # the archived French documentation, no longer maintained
    "docs/cli.md",  # generated from the CLI's help strings, which are read at their source
    "packages/contracts/ts/",  # generated from the schemas, which are read at their source
    # Third-party skills, redistributed verbatim under their own licence (ADR 0065): their text is theirs.
    "packages/core/src/choregos_core/skill_catalogue/bundled/",
    # The 1.x joiners-leavers template, French on purpose: what S39-11c's script migrates (S39-11).
    "apps/api/tests/fixtures/joiners-leavers-v1/",
    # The injection guard's patterns also recognise an injection written in French.
    "packages/core/src/choregos_core/injection.py",
)
EXCLUDED_NAMES = {"pnpm-lock.yaml"}
#: Paths whose French name is a record, not a choice: the path pass skips them, each with its reason.
FROZEN_PATHS = {
    f"tests/replay/histories/{stem}.json": "stem is a recorded workflow id (ADR 0098)"
    for stem in ("wi-panier-DEMO-1e", "wi-panier-DEMO-2d", "wi-staffing-RH-1d", "wi-staffing-RH-1f")
}

# ───────────────────────────────────────── the detector ─────────────────────────────────────────

#: Accents of French that English does not use; ü, ö, ä, ñ are left out (German, Spanish names).
_ACCENTS = re.compile(r"[àâçéèêëîïôûùœÀÂÇÉÈÊËÎÏÔÛÙŒ«»]")  # lang: fr-ok
#: Proper names and English loanwords keep their accents.
PROPER_NAMES = ("Léa", "Hélène", "Zoé", "Chloé", "Céline", "André", "Noël", "Québec", "Montréal")
_LOANWORDS = re.compile(
    r"\b(café|cafés|naïve|naïvely|naïveté|résumé|résumés|déjà|façade|façades|cliché|clichés|fiancé|"
    r"protégé|touché|née|exposé|entrée|entrées|crème|élan|vis-à-vis|à\s+la)\b",
    re.IGNORECASE,
)


def _lexicon(*lines: str) -> frozenset[str]:
    return frozenset(word for line in lines for word in line.split())


#: Words no English sentence uses: one is enough in a text of two words or more. The last two lines
#: hold the words that caught `En cours` and `Intervention humaine` in the shipped-text guard of
#: ADR 0039, whose detector `looks_like_shipped_french` below is the former one.
#: `comment`, `faire`, `sans` and `reprise` are English words too, and are left out.
STRONG = _lexicon(
    "dans avec leur leurs chaque sont une cette ces nous vous mais aussi",  # lang: fr-ok
    "alors donc puis rien encore lorsque parce puisque quand peut doit faut",  # lang: fr-ok
    "etre avoir sera sinon depuis selon toujours jamais ainsi voici celui",  # lang: fr-ok
    "celle ceux dont lequel laquelle quel quelle aucun aucune autre autres",  # lang: fr-ok
    "tous toutes fois chez ici cela ça quoi pourquoi doute peu lire",  # lang: fr-ok
    "cours ouvert ouverte humain humaine cadrage relecture besoin demande",  # lang: fr-ok
    "poste compte groupe commande ecris verifie",  # lang: fr-ok
)
#: French function words English text can contain once (`de facto`, `en route`, `et al.`):
#: two are needed, in lowercase or capitalised — `EST`, `DES`, `AU` are acronyms.
WEAK = _lexicon(
    "les des est pour par sur du au aux ou ne et le la un ce il elle que",  # lang: fr-ok
    "qui pas en de se sa ses tout sous vers ni lui",  # lang: fr-ok
)
#: `l'état`, `d'un`, `qu'il`, `n'est`, `c'est`; `D'Angelo` and `O'Brien` are names.
_CONTRACTION = re.compile(r"^(?:[ldnjcmst]|qu|L|C|J|N|S|Qu)['’][a-zà-ÿ]")
_EDGE_PUNCTUATION = "\"'`.,;:!?()[]{}«»…*_~<>|“”‘’-–—"
#: A token with one of these is code, a path, an URL or a number, not a word.
_NOT_A_WORD = re.compile(r"[/\\=@<>{}()\[\]#$%*|~^+0-9_]|\.\w|[a-z][A-Z]")
_CODE_SPAN = re.compile(r"`[^`]*`")
_QUOTED_KEY = re.compile(r"([\"'])[\w.\-/:]+\1")
_URL = re.compile(r"https?://\S+")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")


def _words(text: str) -> list[str]:
    text = _URL.sub(" ", _LINK_TARGET.sub("]", text))
    text = _QUOTED_KEY.sub(" ", _CODE_SPAN.sub(" ", text))
    words = []
    for raw in text.split():
        token = raw.strip(_EDGE_PUNCTUATION)
        if token and not _NOT_A_WORD.search(token):
            words.append(token)
    return words


def french_reason(text: str) -> str | None:
    """Why `text` reads as French, or None. Code spans, quoted keys, URLs and identifiers
    (anything with `_`, `/`, a digit, a dot or camelCase) are not read."""
    words = _words(text)
    if not words:
        return None
    prose = " ".join(words)
    for name in PROPER_NAMES:
        prose = prose.replace(name, "")
    prose = _LOANWORDS.sub("", prose)
    accent = _ACCENTS.search(prose)
    if accent:
        return f"French accent {accent.group()!r}"
    for word in words:
        if _CONTRACTION.match(word):
            return f"French contraction {word!r}"
    if len(words) < 2:
        return None  # a lone word is a key or a name, unless it carries an accent
    lowered = [w.lower() for piece in words for w in piece.split("-")]
    strong = [w for w in lowered if w in STRONG]
    if strong:
        return f"French word {strong[0]!r}"
    weak = [
        w for piece in words for w in piece.split("-") if w in WEAK or (w.istitle() and w.lower() in WEAK)
    ]
    if len(weak) >= 2:
        return f"French function words {' '.join(weak[:4])!r}"
    return None


def looks_french(text: str) -> bool:
    return french_reason(text) is not None


#: Words an identifier can carry that are French and not English (S39-11): the vocabulary a shipped
#: template used before 2.0.0, and its near neighbours. A word that is also an English word
#: (`plan`, `present`, `service`, `contract`, `group`, `date`, `badge`) is deliberately absent: the
#: lexicon is a net for French identifiers, not a dictionary — what it does not know it does not
#: flag, so a new French word is added here the day it is found.
IDENTIFIER_WORDS = _lexicon(
    "demande valide comptes compte sensible poste postes actif pret coupe",  # lang: fr-ok
    "coupure reprise recu efface effacement clos coordinateur rh accueil",  # lang: fr-ok
    "plateforme arrivee arrivees depart departs nom groupe groupes adresse",  # lang: fr-ok
    "modele etat materiel materiels collaborateur collaborateurs contrat",  # lang: fr-ok
    "contrats matricule statut parti venir deja consigner consigne",  # lang: fr-ok
    "constater controle ouvert ouvre ferme remis remise suivi numero serie",  # lang: fr-ok
    "porteur titulaire detenteur lecteurs annuaire parc transporteur",  # lang: fr-ok
    "fournisseur commande commander expedie inscrit repris cdi cdd",  # lang: fr-ok
    "alternance prestation debut fin paie ventes dossiers salaries profils",  # lang: fr-ok
    "acces liens abime manquant revoir activer jour jours",  # lang: fr-ok
)
_IDENTIFIER_PART = re.compile(r"[A-Za-z]+")


def french_identifier_words(identifier: str) -> list[str]:
    """The French words an identifier (`date_arrivee`, `t-validation`, `depart.coupure`) is made of:
    split on every non-letter, camelCase kept whole; empty when it is English as far as the lexicon
    knows. Prose is read by `french_reason`; this reads the keys prose detection skips."""
    return [
        w for w in (m.group().lower() for m in _IDENTIFIER_PART.finditer(identifier)) if w in IDENTIFIER_WORDS
    ]


# ───────────────────────────── the shipped-text detector (ADR 0039) ─────────────────────────────

#: Proper names keep their accents in what the platform ships.
SHIPPED_PROPER_NAMES = ("Léa", "Hélène", "Zoé", "Varga")  # lang: fr-ok
_SHIPPED_ACCENTS = re.compile(r"[àâçéèêëîïôûùüÿœÀÂÇÉÈÊËÎÏÔÛÙÜŸŒ«»]")  # lang: fr-ok
_SHIPPED_FUNCTION_WORDS = re.compile(
    r"\b(les|des|une|est|sont|vers|pour|dans|avec|aux|pas|qui|que|leur|chaque|ne|ou|au|du|sur|par)\b",
    re.IGNORECASE,
)
#: Words without an accent that no English label uses: `En cours`, `Intervention humaine` and
#: `Cadrage` passed without them.
_SHIPPED_LEXICON = re.compile(
    r"\b(en|cours|ouverte?|humaine?|cadrage|relecture|besoin|demande|poste|compte|groupe|reprise|commande|"
    r"tu|lis|ecris|verifie)\b",
    re.IGNORECASE,
)


def looks_like_shipped_french(text: str) -> bool:
    """True for an accent outside the proper names, a word of the lexicon, or two French function
    words. It reads what the platform ships (labels, descriptions, instructions, messages) and is
    deliberately blunter than `french_reason`: its false positives are settled by the lists above.
    This is the detector of ADR 0039 §4, which the language tests held until ADR 0098."""
    without_names = text
    for name in SHIPPED_PROPER_NAMES:
        without_names = without_names.replace(name, "")
    if _SHIPPED_ACCENTS.search(without_names) or _SHIPPED_LEXICON.search(without_names):
        return True
    return len(_SHIPPED_FUNCTION_WORDS.findall(without_names)) >= 2


def shipped_prose(text: str) -> str:
    """`text` without what is not prose: code blocks and spans, Jinja placeholders and statements."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    return re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text, flags=re.S)


#: Code that says French on purpose, and why: the injection guard's patterns also recognise an
#: injection written in French. A line here is a decision, not an oversight.
SPOKEN_FRENCH_ON_PURPOSE = frozenset({"packages/core/src/choregos_core/injection.py"})


def spoken_strings(source: str) -> Iterator[tuple[int, str]]:
    """(line, text) for the strings of a Python module that can reach someone: not docstrings, not
    comments written as strings, not regular-expression patterns; an f-string is read without its
    holes."""
    tree = ast.parse(source)
    silent: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            silent.add(id(node.value))
        if isinstance(node, ast.JoinedStr):
            silent.update(id(part) for part in node.values)
            yield (
                node.lineno,
                "".join(
                    part.value
                    for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                ),
            )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in silent
            and "\\b" not in node.value
        ):
            yield node.lineno, node.value


def french_spoken_strings(root: str) -> list[str]:
    """`path:line: text` for every spoken string of the Python modules under `root` (relative to the
    repository) that `looks_like_shipped_french`, tests and `SPOKEN_FRENCH_ON_PURPOSE` aside."""
    faults = []
    for file in sorted((ROOT / root).rglob("*.py")):
        relative = file.relative_to(ROOT).as_posix()
        if relative in SPOKEN_FRENCH_ON_PURPOSE or "/tests/" in relative:
            continue
        faults += [
            f"{relative}:{line}: {text[:90]!r}"
            for line, text in spoken_strings(file.read_text(encoding="utf-8"))
            if looks_like_shipped_french(text)
        ]
    return faults


# ───────────────────────────────── French names in code (ADR 0098) ─────────────────────────────────

#: Words a code identifier or a path carries that are French and not English: `IDENTIFIER_WORDS`
#: less the ones English uses (`depart` is a verb: `depart_now`, `/trains/{env}/depart`; `coupe` is
#: a car), plus the strong words below. The lexicon is a net: what it does not know it does not flag.
#: A prototype found no English word among the hits of the strong words on the English packages, and
#: `lister` is out of it for that reason: `lister = getattr(client, "list_tools", None)` is English.
CODE_STRONG_WORDS = _lexicon(
    "acces accessibilite activite activites adaptateur adaptateurs affiche ajout ajoute",  # lang: fr-ok
    "ancien ancienne anciens anglais annonce annoncee appartient appel appelant appeler",  # lang: fr-ok
    "appels applicatif approbateur approbation apres arbre arrete arrondi attendre",  # lang: fr-ok
    "attendu attendue attente aucun aucune authentification autre autres avec besoin",  # lang: fr-ok
    "besoins bibliotheque bilan bloque boite branche brouillon brut cadrage capacite",  # lang: fr-ok
    "capacites caracteres carte cartes casse chaque champ champs changement charger",  # lang: fr-ok
    "chemin chemins choisir choix cible cibles cle cles cloturer coeur commande comme",  # lang: fr-ok
    "compenser composant comptes conclure confirme conformite connecteur connecteurs",  # lang: fr-ok
    "consigne consigner constructeur contenu contexte contraste contrats controler",  # lang: fr-ok
    "controles coquille corps couleur couleurs courtier cout couts coutures couvre creer",  # lang: fr-ok
    "dans debordement debut declarer decouverte deduites defaut defauts delai demandes",  # lang: fr-ok
    "demarre demarrer demarrage densite depense depenses deploiement derniere dernier",  # lang: fr-ok
    "derriere deux developpement donnees dossiers droit droits duree durci ecoute ecrire",  # lang: fr-ok
    "ecriture editeur effet effets embarquement empreinte entete entetes entree entrees",  # lang: fr-ok
    "entreprise envoi epingle equipe erreur erreurs escalade essai essais etape etapes",  # lang: fr-ok
    "etats etiquette etiquettes evenement evenements executer executeur executeurs",  # lang: fr-ok
    "exemple exemples exigence exigences externe externes fait faits familles fantomes",  # lang: fr-ok
    "faux fiche fichier fichiers fournisseurs fraiche fraicheur frontiere gabarit",  # lang: fr-ok
    "gabarits garanties garde gardes gele geles geste gestes gouvernee gouvernees",  # lang: fr-ok
    "gouvernes graphe greffon greffons grilles historique humain humaine humains identite",  # lang: fr-ok
    "illisible indisponible installateur installateurs installe integre interne",  # lang: fr-ok
    "introuvable irreversibles jeton jetons laisse laterale lecteur ligne lignes limiteur",  # lang: fr-ok
    "lire lisible liste livraison livre livres locataire locaux logiciel logicielle",  # lang: fr-ok
    "longueur maitrisee manifeste marquer matrice membre memoire mesures metier metriques",  # lang: fr-ok
    "migrer modeles montee montes mort moteur mots naissance noeud nombre noms nouveau",  # lang: fr-ok
    "nouvelle nouveaux observabilite onglets ontologie operabilite optionnelle oubli",  # lang: fr-ok
    "outil outils panier panneau panneaux paquet paquets parametres parcours paternite",  # lang: fr-ok
    "perdu perimetre perte pilote planification plusieurs politique porte portee portees",  # lang: fr-ok
    "pourquoi preparer preuve preuves privees processus produit projet projets publier",  # lang: fr-ok
    "quand racine raison ranger rapport rattache rattrapage recus refus refusee regle",  # lang: fr-ok
    "reglages reglee reinitialiser relais relaie relecture rendre renommer repartition",  # lang: fr-ok
    "reponse requete restauration resultat resultats retirer retrait rien routage routee",  # lang: fr-ok
    "routeurs saveur securite seuil seul seule serveur servis sorte sortes sortie sorties",  # lang: fr-ok
    "statut stocke suivant supplementaires tache taches taille tentative texte titre tous",  # lang: fr-ok
    "toujours tout trajectoire trouve trouver une utilisateur utilitaires valeur valeurs",  # lang: fr-ok
    "valider verite verrous verticale visuel voisinage voit",  # lang: fr-ok
)
CODE_IDENTIFIER_WORDS = (IDENTIFIER_WORDS - {"depart", "departs", "coupe"}) | CODE_STRONG_WORDS
#: French function words an English name can carry once (`n`, `d`, `l` are loop variables, `en` an
#: abbreviation); two DISTINCT ones make a name French: `test_l_index_de_la_page`. `a` is out of
#: it (an article in English) and so is `ont`.
CODE_IDENTIFIER_WEAK = _lexicon(
    "le la les de du des un et en au aux l d n qu ce se sa ses son par pour sur sous sans",  # lang: fr-ok
    "qui que ne pas plus est",  # lang: fr-ok
)
_CAMEL_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")
_NOT_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]+")


def _code_words(name: str) -> list[str]:
    """The lowercase words of a name: split on every non-alphanumeric, a token that holds a digit
    dropped (`a2a`, `ont001`, `S3Bucket`: the lexicon has no word with a digit), then camelCase
    split (`RapportAcces` is `rapport`, `acces`; `McpClient` is `mcp`, `client`)."""
    words = []
    for token in _NOT_ALPHANUMERIC.split(name):
        if token and not any(char.isdigit() for char in token):
            words += [word.lower() for word in _CAMEL_WORD.findall(token)]
    return words


def code_identifier_reason(name: str) -> str | None:
    """Why `name` is a French identifier, or None: one word of `CODE_IDENTIFIER_WORDS`, or at
    least two distinct words of `CODE_IDENTIFIER_WEAK`."""
    words = _code_words(name)
    strong = [word for word in words if word in CODE_IDENTIFIER_WORDS]
    if strong:
        return f"French identifier word {strong[0]!r}"
    weak = sorted({word for word in words if word in CODE_IDENTIFIER_WEAK})
    if len(weak) >= 2:
        return f"French function words {' '.join(weak)!r}"
    return None


def path_reason(path: str) -> str | None:
    """Why a component of `path` is French, or None. A component is judged whole, extension
    included (`greffons.py`), and a frozen path is never French (`FROZEN_PATHS`)."""
    if path in FROZEN_PATHS:
        return None
    for component in path.split("/"):
        reason = code_identifier_reason(component)
        if reason:
            return f"{reason} in the path component {component!r}"
    return None


def python_declarations(content: str) -> Iterator[tuple[int, str]]:
    """(line, name) for every name a Python file declares: functions, classes, every parameter,
    every name or attribute stored to, every `import … as`. A name that is only used is not read."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # an invalid escape sequence in a string is not our business
            tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node.lineno, node.name
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            yield node.end_lineno or node.lineno, node.attr
        elif isinstance(node, ast.alias) and node.asname:
            yield node.lineno, node.asname


_TS_DECLARATION = re.compile(r"\b(?:function|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)")


def ts_declarations(content: str) -> Iterator[tuple[int, str]]:
    """(line, name) for every `function`, `const`, `let`, `var`, `class`, `interface`, `type` and
    `enum` a TypeScript or JavaScript file declares. Comments and strings are blanked first, newlines
    kept, so a name inside one is not read and the line numbers hold."""
    blanked = _TS_TOKEN.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), content)
    line, position = 1, 0
    for match in _TS_DECLARATION.finditer(blanked):
        line += blanked.count("\n", position, match.start(1))
        position = match.start(1)
        yield line, match.group(1)


# ─────────────────────────────── what is prose, language by language ───────────────────────────────


_STRING_PREFIX = re.compile(r"^[rRbBuU]{1,2}(?=[\"'])")


def _python_prose(content: str) -> Iterator[tuple[int, str]]:
    """Comments, docstrings and strings, line by line; an identifier is never read."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        yield from enumerate(content.splitlines(), start=1)
        return
    for token in tokens:
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string.lstrip("#")
        elif token.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
            if "\\b" in token.string:
                continue  # a regular expression, not a sentence
            text = _STRING_PREFIX.sub("", token.string)
            for offset, line in enumerate(text.splitlines()):
                yield token.start[0] + offset, line


_TS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`",
    re.S,
)
#: JSX text: what sits between a tag's `>` and the next `<` or `{` — not an arrow's `=>`.
_JSX_TEXT = re.compile(r"(?<=[\w\"'}/])>(?!=)([^<>{}]+)")


def _ts_prose(content: str, *, jsx: bool) -> Iterator[tuple[int, str]]:
    position = 0
    for match in _TS_TOKEN.finditer(content):
        if jsx:
            yield from _jsx_text(content, position, match.start())
        line = content.count("\n", 0, match.start()) + 1
        for offset, piece in enumerate(match.group().splitlines()):
            yield line + offset, piece.strip("/*`'\" ")
        position = match.end()
    if jsx:
        yield from _jsx_text(content, position, len(content))


def _jsx_text(content: str, start: int, end: int) -> Iterator[tuple[int, str]]:
    for match in _JSX_TEXT.finditer(content, start, end):
        line = content.count("\n", 0, match.start()) + 1
        for offset, piece in enumerate(match.group(1).splitlines()):
            yield line + offset, piece


_FENCE = re.compile(r"^\s*(```|~~~)")


def _markdown_prose(content: str) -> Iterator[tuple[int, str]]:
    in_fence = False
    for number, line in enumerate(content.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, re.sub(r"<!--.*?-->", "", line)


_YAML_KEY = re.compile(r"^\s*(?:-\s+)?[\w.\-/\"']+\s*:(?=\s|$)")


def _yaml_prose(content: str) -> Iterator[tuple[int, str]]:
    """A key is an identifier; its value, a block scalar's lines and comments are read."""
    for number, line in enumerate(content.splitlines(), start=1):
        yield number, _YAML_KEY.sub("", line).lstrip(" -")


def _makefile_prose(content: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(content.splitlines(), start=1):
        if "#" in line:
            yield number, line.split("#", 1)[1].lstrip("#")


def _sql_prose(content: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(content.splitlines(), start=1):
        if "--" in line:
            yield number, line.split("--", 1)[1].lstrip("-")


_MARKUP = re.compile(r"<[^>]*>|\{%.*?%\}|\{\{.*?\}\}")


def _markup_prose(content: str) -> Iterator[tuple[int, str]]:
    """HTML and Jinja: the text of a line once its tags, statements and placeholders are removed."""
    for number, line in enumerate(content.splitlines(), start=1):
        yield number, _MARKUP.sub(" ", line)


_HASH_COMMENT_SUFFIXES = {".sh", ".toml", ".ini", ".Dockerfile"}
_HASH_COMMENT_NAMES = {"Dockerfile", "justfile", "CODEOWNERS", "Makefile"}
_SCRIPT_SUFFIXES = {".js", ".mjs", ".mts", ".css", ".rs"}


def prose_lines(path: str, content: str) -> Iterator[tuple[int, str]]:
    """(line number, prose) for every piece of prose in a file, by its kind."""
    pure = pathlib.PurePosixPath(path)
    suffix = pure.suffix
    if suffix == ".py":
        return _python_prose(content)
    if suffix in (".ts", ".tsx"):
        return _ts_prose(content, jsx=suffix == ".tsx")
    if suffix in _SCRIPT_SUFFIXES:
        return _ts_prose(content, jsx=False)
    if suffix == ".md":
        return _markdown_prose(content)
    if suffix in (".yaml", ".yml", ".tpl", ".txt"):
        return _yaml_prose(content)
    if suffix == ".sql":
        return _sql_prose(content)
    if suffix in (".html", ".j2"):
        return _markup_prose(content)
    # `#` comments: shell, TOML, INI, Dockerfiles, justfile, CODEOWNERS and the Makefile.
    return _makefile_prose(content)


def is_scanned(path: str) -> bool:
    pure = pathlib.PurePosixPath(path)
    if path.startswith(EXCLUDED) or pure.name in EXCLUDED_NAMES:
        return False
    if path == "Makefile" or path in SCANNED_ROOT_FILES:
        return True
    if pure.suffix not in SUFFIXES and pure.name not in SCANNED_NAMES:
        return False
    return (len(pure.parts) == 1 and pure.suffix == ".md") or path.startswith(SCANNED_DIRS)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str
    text: str
    #: Which pass found it: "prose", "identifier" or "path" (ADR 0098).
    kind: str = "prose"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}: {self.text.strip()[:100]}"


def findings_in(path: str, content: str, added: set[int]) -> list[Finding]:
    """French in the prose of the `added` lines of a file whose new content is `content`."""
    lines = content.splitlines()
    found = []
    for number, prose in prose_lines(path, content):
        if number not in added or ESCAPE in lines[number - 1]:
            continue
        reason = french_reason(prose)
        if reason:
            found.append(Finding(path, number, reason, lines[number - 1]))
    return found


# ──────────────────────────── a whole file: the three passes (ADR 0098) ────────────────────────────


def has_file_escape(content: str) -> bool:
    """True when one of the first five lines carries `FILE_ESCAPE`."""
    return any(FILE_ESCAPE in line for line in content.split("\n", 5)[:5])


def _declarations(path: str, content: str) -> Iterator[tuple[int, str]]:
    suffix = pathlib.PurePosixPath(path).suffix
    if suffix == ".py":
        return python_declarations(content)
    if suffix in (".ts", ".tsx", ".mts", ".js", ".mjs"):
        return ts_declarations(content)
    return iter(())


def file_findings(path: str, content: str) -> list[Finding]:
    """The French in one file, by pass, one finding per unit counted (ADR 0098):

    - prose: one per French prose line, a line carrying `ESCAPE` not read;
    - identifier: one per DISTINCT French name the file declares, at its first declaration that
      does not carry `ESCAPE` (a name declared only on escaped lines is not counted);
    - path: one for the file when a component of its path is French.

    A file with `FILE_ESCAPE` in its first five lines has no finding."""
    if has_file_escape(content):
        return []
    lines = content.splitlines()
    found: list[Finding] = []
    reported: set[int] = set()
    for number, prose in prose_lines(path, content):
        if number in reported or not 1 <= number <= len(lines) or ESCAPE in lines[number - 1]:
            continue
        reason = french_reason(prose)
        if reason:
            reported.add(number)
            found.append(Finding(path, number, reason, lines[number - 1]))
    declared: dict[str, list[int]] = defaultdict(list)
    for number, name in _declarations(path, content):
        if 1 <= number <= len(lines) and ESCAPE not in lines[number - 1]:
            declared[name].append(number)
    for name, numbers in sorted(declared.items(), key=lambda item: min(item[1])):
        reason = code_identifier_reason(name)
        if reason:
            number = min(numbers)
            found.append(Finding(path, number, reason, lines[number - 1], "identifier"))
    reason = path_reason(path)
    if reason:
        found.append(Finding(path, 0, reason, path, "path"))
    return found


# ───────────────────────────────────────────── git ─────────────────────────────────────────────

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(diff: str) -> dict[str, set[int]]:
    """{path: numbers of the lines added in the new file} from a `--unified=0` diff."""
    result: dict[str, set[int]] = {}
    path: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path is not None and (hunk := _HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or "1")
            result.setdefault(path, set()).update(range(start, start + count))
    return result


GIT = shutil.which("git") or "git"


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - a fixed program; the arguments are refs and paths
        [GIT, *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _ends(diff_range: str) -> tuple[str, str]:
    """(base, head) of `A...B` (base = merge base) or `A..B`."""
    if "..." in diff_range:
        left, right = diff_range.split("...", 1)
        right = right or "HEAD"
        return _git("merge-base", left, right).strip(), right
    left, _, right = diff_range.partition("..")
    return left, right or "HEAD"


def scan_range(base: str, head: str) -> list[Finding]:
    # `-C`: a file renamed with a redirect left at its old name is a copy, not new lines (S34-05).
    diff = _git(
        "diff", "--unified=0", "--no-color", "--no-ext-diff", "-M", "-C", "--diff-filter=ACMR", base, head
    )
    found = []
    for path, added in sorted(added_lines(diff).items()):
        if is_scanned(path):
            found += findings_in(path, _git("show", f"{head}:{path}"), added)
    return found


_TRAILER = re.compile(r"^[A-Za-z-]+: .+<.+@.+>$")


def scan_commits(base: str, head: str) -> list[Finding]:
    """Commit messages of `base..head`, trailers (`Signed-off-by: …`) aside."""
    found = []
    for sha in _git("rev-list", "--no-merges", f"{base}..{head}").split():
        for number, line in enumerate(_git("show", "-s", "--format=%B", sha).splitlines(), start=1):
            if _TRAILER.match(line) or ESCAPE in line:
                continue
            reason = french_reason(line)
            if reason:
                found.append(Finding(f"commit {sha[:10]}", number, reason, line))
    return found


def _in_path_scope(path: str) -> bool:
    """A tracked file whose name the path pass judges: under a scanned directory or at the root,
    and not excluded."""
    pure = pathlib.PurePosixPath(path)
    if path.startswith(EXCLUDED) or pure.name in EXCLUDED_NAMES:
        return False
    return len(pure.parts) == 1 or path.startswith(SCANNED_DIRS)


def scan_tree(prefixes: Iterable[str] = ("",)) -> list[Finding]:
    """The three passes over every tracked file under one of `prefixes` (`git ls-files`, the working
    tree's content). The prose and identifier passes read the files `is_scanned` accepts; the path
    pass reads every tracked file in scope. Raises `RuntimeError` outside a git checkout."""
    try:
        listing = _git("ls-files", "-z")
    except (subprocess.CalledProcessError, OSError):
        raise RuntimeError("not a git checkout") from None
    wanted = tuple(prefixes)
    found: list[Finding] = []
    for path in sorted(name for name in listing.split("\0") if name):
        if not path.startswith(wanted) or not _in_path_scope(path):
            continue
        file = ROOT / path
        if file.is_symlink() or not file.is_file():
            head = ""
        elif is_scanned(path):
            found += file_findings(path, file.read_text(encoding="utf-8", errors="replace"))
            continue
        else:
            with file.open("rb") as handle:
                head = handle.read(2048).decode("utf-8", errors="replace")
        reason = path_reason(path)
        if reason and not has_file_escape(head):
            found.append(Finding(path, 0, reason, path, "path"))
    return found


# ──────────────────────────────── areas and baselines (ADR 0098) ────────────────────────────────

_API = "apps/api/src/choregos_api/"
#: One baseline file per area, so that no two stories write the same file. The longest prefix wins:
#: `apps/api/tests/` is `api-tests`, not `api-root`; `""` catches everything else.
AREAS: dict[str, tuple[str, ...]] = {
    "api-mcp": (f"{_API}mcp/",),
    "api-routers": (f"{_API}routers/",),
    "api-services": (f"{_API}services/",),
    "api-schemas": (f"{_API}schemas/",),
    "api-migrations": (f"{_API}migrations/",),
    "api-subsystems": (f"{_API}db/", f"{_API}a2a/", f"{_API}egress/", f"{_API}knowledge/"),
    "api-tests": ("apps/api/tests/",),
    "api-root": ("apps/api/",),
    "orchestrator-tests": ("apps/orchestrator/tests/",),
    "orchestrator-src": ("apps/orchestrator/",),
    "web": ("apps/web/",),
    "core": ("packages/core/",),
    "adapters": ("packages/adapters/",),
    "runner": ("packages/runner/",),
    "contracts": ("packages/contracts/",),
    "packages-other": ("packages/",),
    "tests-language": (
        "tests/langue/",
        "tests/language/",
        "tests/docs/",
        "tests/ci/",
        "tests/dev/",
        "tests/licences/",
        "tests/integrations/",
        "tests/charts/",
    ),
    "tests": ("tests/",),
    "docs-plan": ("docs/plan/",),
    "docs": ("docs/",),
    "trials": ("essai/", "trials/"),
    "ops": ("charts/", "infra/", "docker/"),
    "repo": ("",),
}
BASELINE_DIR = ROOT / "tools" / "english_baseline"


def area_of(path: str) -> str:
    """The area of a tracked path: the one holding its longest matching prefix."""
    length, area = -1, "repo"
    for name, prefixes in AREAS.items():
        for prefix in prefixes:
            if path.startswith(prefix) and len(prefix) > length:
                length, area = len(prefix), name
    return area


class Change(NamedTuple):
    """A file whose count differs from its baseline: `baseline` is 0 for a file that has none."""

    path: str
    found: int
    baseline: int


def counts_per_file(findings: Iterable[Finding]) -> dict[str, int]:
    """{path: number of findings}, every finding being one unit counted by `file_findings`."""
    counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[finding.path] += 1
    return dict(sorted(counts.items()))


def counts_by_area(counts: Mapping[str, int]) -> dict[str, dict[str, int]]:
    by_area: dict[str, dict[str, int]] = {name: {} for name in AREAS}
    for path, count in counts.items():
        by_area[area_of(path)][path] = count
    return by_area


def _header(area: str) -> str:
    return (
        f"# {area}: French findings per file (ADR 0098). "
        "Counts only go down: tools/check_english.py --update-baseline"
    )


def load_baselines(directory: pathlib.Path = BASELINE_DIR) -> dict[str, dict[str, int]]:
    """{area: {path: count}} from every `<area>.tsv` of `directory`."""
    baselines: dict[str, dict[str, int]] = {}
    for file in sorted(directory.glob("*.tsv")):
        counts: dict[str, int] = {}
        for line in file.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                path, _, count = line.rpartition("\t")
                counts[path] = int(count)
        baselines[file.stem] = counts
    return baselines


def compare(counts: Mapping[str, int], baselines: Mapping[str, int]) -> tuple[list[Change], list[Change]]:
    """(rises, stale) of `counts` against `baselines`. A rise is a count above its baseline, or
    a file with French and no baseline; the build fails on it. A stale entry is a baseline above
    the count, or for a file that is gone or clean: it only asks for `--update-baseline`."""
    rises = [
        Change(path, count, baselines.get(path, 0))
        for path, count in sorted(counts.items())
        if count > baselines.get(path, 0)
    ]
    stale = [
        Change(path, counts.get(path, 0), baseline)
        for path, baseline in sorted(baselines.items())
        if counts.get(path, 0) < baseline
    ]
    return rises, stale


def write_baselines(
    counts: Mapping[str, int], areas: Iterable[str], directory: pathlib.Path, *, init: bool = False
) -> None:
    """Write `<area>.tsv` for each of `areas`: the current counts, zeros and gone files dropped.
    Raises `ValueError`, writing nothing, when a count would rise above the one on file — unless
    `init`, which records today's state of areas that have no baseline yet."""
    selected = list(dict.fromkeys(areas))
    unknown = [area for area in selected if area not in AREAS]
    if unknown:
        raise ValueError(f"unknown area(s): {', '.join(unknown)}")
    by_area = counts_by_area(counts)
    current = load_baselines(directory)
    if not init:
        rises = [
            f"{area}: {change.path}: {change.found} > {change.baseline}"
            for area in selected
            for change in compare(by_area[area], current.get(area, {}))[0]
        ]
        if rises:
            raise ValueError(
                "refusing to raise a baseline (counts only go down; ADR 0098):\n  " + "\n  ".join(rises)
            )
    directory.mkdir(parents=True, exist_ok=True)
    for area in selected:
        lines = [
            _header(area),
            *(f"{path}\t{count}" for path, count in sorted(by_area[area].items()) if count),
        ]
        (directory / f"{area}.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def report(findings: Iterable[Finding], areas: Iterable[str]) -> str:
    """The totals per area and pass, as a table."""
    selected = list(areas)
    totals: dict[str, dict[str, int]] = {area: defaultdict(int) for area in selected}
    for finding in findings:
        area = area_of(finding.path)
        if area in totals:
            totals[area][finding.kind] += 1
    rows: list[tuple[str, ...]] = [("area", "prose", "identifiers", "paths", "total")]
    grand = [0, 0, 0]
    for area in selected:
        kinds = (totals[area]["prose"], totals[area]["identifier"], totals[area]["path"])
        grand = [a + b for a, b in zip(grand, kinds, strict=True)]
        rows.append((area, *(str(n) for n in kinds), str(sum(kinds))))
    rows.append(("TOTAL", *(str(n) for n in grand), str(sum(grand))))
    width = max(len(row[0]) for row in rows)
    return "\n".join(f"{row[0]:<{width}}  " + "  ".join(f"{cell:>11}" for cell in row[1:]) for row in rows)


def _resolves(ref: str) -> bool:
    try:
        _git("rev-parse", "--verify", "--quiet", ref)
    except subprocess.CalledProcessError:
        return False
    return True


def _ratchet(findings: list[Finding], areas: list[str], directory: pathlib.Path, *, strict: bool) -> int:
    """`--all`: every file at or below its baseline. Exit 1 on a rise (and on a stale entry when
    `strict`); a stale entry otherwise only prints the command that lowers it."""
    baselines = load_baselines(directory)
    if not baselines:
        print(f"check_english: no baseline in {directory}; create them with --update-baseline --init")
        return 1
    by_area = counts_by_area(counts_per_file(findings))
    by_path: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_path[finding.path].append(finding)
    rising = 0
    stale_areas = []
    for area in areas:
        rises, stale = compare(by_area[area], baselines.get(area, {}))
        for change in rises:
            rising += 1
            print(f"rise: {area}: {change.path}: {change.found} finding(s), baseline {change.baseline}")
            shown = by_path[change.path]
            for finding in shown[:10]:
                print(f"    {finding}")
            if len(shown) > 10:
                print(f"    … and {len(shown) - 10} more (the default mode names the lines a change adds)")
        if stale:
            stale_areas.append(area)
    for area in stale_areas:
        print(f"stale: {area} — run tools/check_english.py --update-baseline --area {area}")
    if rising:
        print(
            f"\n{rising} file(s) above their French baseline (ADR 0098: counts only go down). Write the "
            f"new code in English; a line that must stay French carries `{ESCAPE}` in a comment, a "
            f"shim `{FILE_ESCAPE}` in its first five lines.",
            file=sys.stderr,
        )
        return 1
    if stale_areas and strict:
        return 1
    left = sum(len(by_path[path]) for area in areas for path in by_area[area])
    print(f"check_english: every file is at or below its baseline ({left} French finding(s) left)")
    return 0


def _tree_mode(args: argparse.Namespace) -> int:
    areas: list[str] = args.area or list(AREAS)
    directory: pathlib.Path = args.baseline_dir
    if args.init and any(directory.glob("*.tsv")):
        print(f"check_english: {directory} already holds baselines; --init does not overwrite them")
        return 1
    try:
        findings = scan_tree()
    except RuntimeError as error:
        print(f"check_english: {error}; the tree scan reads `git ls-files`", file=sys.stderr)
        return 1
    if args.update_baseline:
        try:
            write_baselines(counts_per_file(findings), areas, directory, init=args.init)
        except ValueError as error:
            print(f"check_english: {error}", file=sys.stderr)
            return 1
        print(f"check_english: wrote {len(areas)} baseline file(s) in {directory}")
    if args.report:
        print(report(findings, areas))
    if args.all and not args.update_baseline:
        return _ratchet(findings, areas, directory, strict=args.strict)
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument("--range", dest="diff_range", help=f"git range to read (default {DEFAULT_RANGE})")
    parser.add_argument("--no-commits", action="store_true", help="do not read commit messages")
    parser.add_argument(
        "--all",
        action="store_true",
        help="read every tracked file and fail when a count rises above its baseline (ADR 0098)",
    )
    parser.add_argument(
        "--area", action="append", default=[], choices=list(AREAS), metavar="NAME", help="only this area"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the baselines: lowers counts, drops zeros and gone files, refuses a rise",
    )
    parser.add_argument(
        "--init", action="store_true", help="with --update-baseline: create baselines that do not exist yet"
    )
    parser.add_argument("--report", action="store_true", help="print the totals per area and pass")
    parser.add_argument("--strict", action="store_true", help="with --all: a stale baseline entry fails")
    parser.add_argument(
        "--baseline-dir",
        type=pathlib.Path,
        default=BASELINE_DIR,
        help="where the baselines are (default tools/english_baseline)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.init and not args.update_baseline:
        parser.error("--init needs --update-baseline")
    if args.strict and not args.all:
        parser.error("--strict needs --all")
    if args.all or args.update_baseline or args.report:
        return _tree_mode(args)

    diff_range = args.diff_range or DEFAULT_RANGE
    left = re.split(r"\.\.\.?", diff_range, maxsplit=1)[0]
    if args.diff_range is None and not _resolves(left):
        # A clone without the remote (an archive, a fork's first checkout): nothing to compare to.
        # CI always passes `--range`, and there an unknown ref is an error.
        print(f"check_english: {left} does not resolve; nothing to compare to, skipped")
        return 0
    base, head = _ends(diff_range)
    found = scan_range(base, head)
    if not args.no_commits:
        found += scan_commits(base, head)
    for finding in found:
        print(finding)
    if found:
        print(
            f"\n{len(found)} line(s) in French (ADR 0046: everything new is in English). Name an "
            f"identifier in backticks; a line that must stay French carries `{ESCAPE}` in a comment.",
            file=sys.stderr,
        )
        return 1
    print(f"check_english: no French in the lines added by {diff_range}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
