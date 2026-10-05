# SPDX-License-Identifier: Apache-2.0
"""Éditions typées d'un workflow, greffées dans le TEXTE (ADR 0031).

La carte et la vue processus modifient un workflow sans réécrire son YAML : chaque opération devient
une ou plusieurs greffes — le remplacement d'une plage de caractères — guidées par `yaml.compose`,
qui donne la position de chaque nœud. Le reste du document, commentaires et style compris, n'est pas
touché. `ruamel` réécrirait le style, et il n'est pas une dépendance.

Chaque opération rend son **inverse** : appliquée juste après, elle redonne les octets d'origine.
C'est ce qui permet à la console d'annuler, et ce que les tests vérifient opération par opération.
Pour y parvenir, un élément d'une collection en bloc emporte les commentaires qui le précèdent (ils
le décrivent), et l'inverse d'un retrait porte le texte retiré, tel quel.

Une opération dont la cible n'existe pas est refusée (`EditionRefusee`) ; une opération qui rend le
workflow invalide ne l'est pas : le validateur le dira, et la console n'enregistre pas un workflow
invalide.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

#: Les états dont le NOM porte un effet (`interpreter.py`, `validator.py`) : les renommer ou les
#: retirer change le comportement, pas seulement l'affichage.
PREFIXES_A_EFFET = ("pr_", "merged", "deployed_prod")
IDENTIFIANT = re.compile(r"^[a-z][a-z0-9_-]*$")


class EditionRefusee(ValueError):  # noqa: N818 - un refus motivé, rendu en 422
    pass


# ───────────────────────────── opérations ─────────────────────────────


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AddState(_Op):
    op: Literal["add_state"] = "add_state"
    name: str
    #: `{display, kind, terminal, tracker}` ; `display` vaut le nom par défaut.
    spec: dict[str, Any] | None = None
    #: Le bloc exact à réinsérer : l'inverse d'un retrait.
    text: str | None = None
    index: int | None = None


class RemoveState(_Op):
    op: Literal["remove_state"] = "remove_state"
    name: str


class RenameState(_Op):
    op: Literal["rename_state"] = "rename_state"
    from_: str = Field(alias="from")
    to: str


class _SetField(_Op):
    field: str
    #: La valeur à écrire : un scalaire, ou une collection rendue en flow.
    value: Any = None
    #: Le texte exact de la valeur : l'inverse d'une modification.
    raw: str | None = None
    #: Retirer la clé.
    unset: bool = False
    #: Le bloc exact de la clé à réinsérer, et sa place : l'inverse d'un retrait.
    text: str | None = None
    index: int | None = None


class SetState(_SetField):
    op: Literal["set_state"] = "set_state"
    name: str


class AddTransition(_Op):
    op: Literal["add_transition"] = "add_transition"
    #: `{id, from, to, by, …}` ; sans `id`, `t-<from>-<to>`.
    transition: dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveTransition(_Op):
    op: Literal["remove_transition"] = "remove_transition"
    id: str


class SetTransition(_SetField):
    op: Literal["set_transition"] = "set_transition"
    id: str


class AddGate(_Op):
    op: Literal["add_gate"] = "add_gate"
    transition: str
    gate: str | dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveGate(_Op):
    op: Literal["remove_gate"] = "remove_gate"
    transition: str
    name: str
    #: Sa place, quand on la connaît : l'inverse d'un ajout la donne.
    index: int | None = None


class AddActor(_Op):
    op: Literal["add_actor"] = "add_actor"
    name: str
    spec: dict[str, Any] | None = None
    text: str | None = None
    index: int | None = None


class RemoveActor(_Op):
    op: Literal["remove_actor"] = "remove_actor"
    name: str


class SetActor(_SetField):
    op: Literal["set_actor"] = "set_actor"
    name: str


Operation = Annotated[
    AddState
    | RemoveState
    | RenameState
    | SetState
    | AddTransition
    | RemoveTransition
    | SetTransition
    | AddGate
    | RemoveGate
    | AddActor
    | RemoveActor
    | SetActor,
    Field(discriminator="op"),
]
OPERATIONS: TypeAdapter[list[Operation]] = TypeAdapter(list[Operation])


@dataclass
class Edition:
    """Le texte édité, les opérations qui l'annulent (dans l'ordre où les jouer), les avertissements."""

    yaml: str
    inverse: list[Any] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)


def editer(texte: str, operations: list[Any]) -> Edition:
    """Applique les opérations dans l'ordre ; l'inverse les défait dans l'ordre contraire."""
    edition = Edition(yaml=texte)
    for operation in OPERATIONS.validate_python(operations):
        appliquer = _APPLICATIONS[operation.op]
        edition.yaml, inverse, avertissements = appliquer(edition.yaml, operation)
        edition.inverse.insert(0, inverse)
        edition.avertissements.extend(avertissements)
    return edition


# ───────────────────────────── lecture du texte ─────────────────────────────


def _composer(texte: str) -> MappingNode:
    try:
        racine = yaml.compose(texte, Loader=yaml.SafeLoader)
    except yaml.YAMLError as erreur:
        raise EditionRefusee(f"YAML illisible : {erreur}") from erreur
    if not isinstance(racine, MappingNode):
        raise EditionRefusee("le document doit être un objet YAML")
    return racine


def _cle(mapping: Node | None, nom: str) -> tuple[ScalarNode, Node] | None:
    if isinstance(mapping, MappingNode):
        for cle, valeur in mapping.value:
            if isinstance(cle, ScalarNode) and cle.value == nom:
                return cle, valeur
    return None


def _valeur(mapping: Node | None, nom: str) -> Node | None:
    trouve = _cle(mapping, nom)
    return trouve[1] if trouve is not None else None


def _index_de_cle(mapping: Node, nom: str) -> int:
    assert isinstance(mapping, MappingNode)
    return next(
        i for i, (cle, _) in enumerate(mapping.value) if isinstance(cle, ScalarNode) and cle.value == nom
    )


def _fin_contenu(noeud: Node) -> int:
    """Où finit le dernier caractère SIGNIFIANT d'un nœud.

    Un bloc finit, pour PyYAML, au jeton suivant — la ligne d'après, commentaires compris ; ses
    feuilles, scalaires et collections en flow, finissent exactement.
    """
    if isinstance(noeud, ScalarNode) or getattr(noeud, "flow_style", False):
        return int(noeud.end_mark.index)
    enfants: list[Node] = []
    if isinstance(noeud, MappingNode):
        for cle, valeur in noeud.value:
            enfants += [cle, valeur]
    elif isinstance(noeud, SequenceNode):
        enfants = list(noeud.value)
    return max((_fin_contenu(e) for e in enfants), default=int(noeud.start_mark.index))


def _fin_de_ligne(texte: str, i: int) -> int:
    if i > 0 and texte[i - 1] == "\n":
        return i
    j = texte.find("\n", i)
    return len(texte) if j < 0 else j + 1


def _debut_de_ligne(texte: str, i: int) -> int:
    return texte.rfind("\n", 0, i) + 1


def _rendre(valeur: Any) -> str:
    rendu = yaml.safe_dump(valeur, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10**6)
    if rendu.endswith("\n...\n"):
        rendu = rendu[: -len("\n...\n")]
    return rendu.strip()


def _greffer(texte: str, debut: int, fin: int, nouveau: str) -> str:
    return texte[:debut] + nouveau + texte[fin:]


# ───────────────────────────── collections ─────────────────────────────


@dataclass
class _Collection:
    """Une collection du document — mapping ou séquence, en bloc ou en flow.

    `borne` : où commencent les lignes de son premier élément, la fin de la ligne de sa clé. Un objet
    en bloc, élément d'une séquence, n'a pas de clé : son premier champ partage la ligne du tiret,
    et `premier_fige` interdit d'y toucher.
    """

    texte: str
    nom: str
    noeud: Node
    borne: int
    premier_fige: bool = False

    @property
    def en_flow(self) -> bool:
        return bool(getattr(self.noeud, "flow_style", False))

    @property
    def taille(self) -> int:
        return len(self.noeud.value) if isinstance(self.noeud, MappingNode | SequenceNode) else 0

    def _bornes(self) -> list[tuple[int, int]]:
        """(début, fin) du contenu de chaque élément."""
        if isinstance(self.noeud, MappingNode):
            return [
                (int(c.start_mark.index), max(_fin_contenu(c), _fin_contenu(v))) for c, v in self.noeud.value
            ]
        return [(int(e.start_mark.index), _fin_contenu(e)) for e in self.noeud.value]

    def _blocs(self) -> list[tuple[int, int]]:
        """Les lignes de chaque élément d'une collection en bloc, commentaires de tête compris."""
        precedent, blocs = self.borne, []
        for _, fin in self._bornes():
            fin_bloc = _fin_de_ligne(self.texte, fin)
            blocs.append((precedent, fin_bloc))
            precedent = fin_bloc
        return blocs

    def _figer(self, i: int) -> None:
        if self.premier_fige and not self.en_flow and i == 0:
            raise EditionRefusee(
                f"le premier champ de `{self.nom}` partage la ligne du tiret : modifiez le YAML"
            )

    def retirer(self, i: int) -> tuple[str, str]:
        """Le texte sans l'élément `i`, et le texte retiré (pour l'inverse)."""
        self._figer(i)
        if self.en_flow:
            bornes = self._bornes()
            if len(bornes) == 1:
                debut, fin = int(self.noeud.start_mark.index) + 1, int(self.noeud.end_mark.index) - 1
            elif i < len(bornes) - 1:
                debut, fin = bornes[i][0], bornes[i + 1][0]
            else:
                debut, fin = bornes[i - 1][1], bornes[i][1]
        else:
            debut, fin = self._blocs()[i]
            if fin == len(self.texte) and not self.texte.endswith("\n") and debut > 0:
                debut -= 1  # le dernier élément d'un fichier sans fin de ligne emporte la précédente
        return _greffer(self.texte, debut, fin, ""), self.texte[debut:fin]

    def _position(self, i: int) -> int:
        n = self.taille
        if not 0 <= i <= n:
            raise EditionRefusee(f"place {i} hors de `{self.nom}` ({n} éléments)")
        if i < n:
            self._figer(i)
        if self.en_flow:
            bornes = self._bornes()
            if n == 0:
                return int(self.noeud.start_mark.index) + 1
            return bornes[i][0] if i < n else bornes[-1][1]
        blocs = self._blocs()
        if not blocs:
            raise EditionRefusee(f"`{self.nom}` est vide : écrivez-la en flow (`[]` ou `{{}}`)")
        return blocs[i][0] if i < n else blocs[-1][1]

    def inserer_brut(self, i: int, brut: str) -> str:
        """Réinsère, à la place `i`, le texte qu'un retrait a rendu."""
        position = self._position(i)
        return _greffer(self.texte, position, position, brut)

    def inserer(self, i: int, element: str) -> str:
        """Insère un élément NEUF : `cle: valeur` d'un mapping, ou `- valeur` d'une séquence."""
        n = self.taille
        position = self._position(i)
        if self.en_flow:
            brut = element if n == 0 else (f"{element}, " if i < n else f", {element}")
            return _greffer(self.texte, position, position, brut)
        # Le retrait du premier élément : devant le tiret d'une séquence ; sous la première clé d'un
        # mapping, même quand elle partage la ligne d'un tiret.
        premier = self._bornes()[0][0]
        avant = self.texte[_debut_de_ligne(self.texte, premier) : premier]
        if isinstance(self.noeud, SequenceNode):
            retrait = avant[: len(avant) - len(avant.lstrip())]
        else:
            retrait = " " * len(avant)
        ligne = f"{retrait}{element}"
        brut = f"\n{ligne}" if position == len(self.texte) and not self.texte.endswith("\n") else f"{ligne}\n"
        return _greffer(self.texte, position, position, brut)


def _sous_collection(texte: str, cle: ScalarNode, noeud: Node) -> _Collection:
    return _Collection(texte, str(cle.value), noeud, _fin_de_ligne(texte, int(cle.end_mark.index)))


def _element_de_sequence(texte: str, nom: str, element: Node) -> _Collection:
    """Un objet élément d'une séquence : ses champs commencent au premier, sur la ligne du tiret."""
    return _Collection(texte, nom, element, int(element.start_mark.index), premier_fige=True)


def _collection(texte: str, parent: Node, nom: str) -> _Collection:
    trouve = _cle(parent, nom)
    if trouve is None:
        raise EditionRefusee(f"pas de `{nom}` ici")
    cle, noeud = trouve
    if not isinstance(noeud, MappingNode | SequenceNode):
        raise EditionRefusee(f"`{nom}` n'est pas une collection")
    return _sous_collection(texte, cle, noeud)


def _modifier_cle(texte: str, porteur: _Collection, op: _SetField) -> tuple[str, dict[str, Any]]:
    """`field` d'un élément : écrite, remplacée, retirée ou réinsérée ; rend de quoi l'annuler."""
    if not isinstance(porteur.noeud, MappingNode):
        raise EditionRefusee(f"`{porteur.nom}` n'est pas un objet")
    trouve = _cle(porteur.noeud, op.field)
    if op.text is not None:
        if trouve is not None:
            raise EditionRefusee(f"`{op.field}` existe déjà")
        place = porteur.taille if op.index is None else op.index
        return porteur.inserer_brut(place, op.text), {"field": op.field, "unset": True}
    if op.unset:
        if trouve is None:
            raise EditionRefusee(f"`{op.field}` n'existe pas")
        place = _index_de_cle(porteur.noeud, op.field)
        sans, retire = porteur.retirer(place)
        return sans, {"field": op.field, "text": retire, "index": place}
    nouveau = op.raw if op.raw is not None else _rendre(op.value)
    if trouve is None:
        return porteur.inserer(porteur.taille, f"{op.field}: {nouveau}"), {"field": op.field, "unset": True}
    valeur = trouve[1]
    if isinstance(valeur, MappingNode | SequenceNode) and not valeur.flow_style:
        raise EditionRefusee(f"`{op.field}` est écrit en bloc : modifiez-le dans le YAML")
    debut, fin = int(valeur.start_mark.index), int(valeur.end_mark.index)
    return _greffer(texte, debut, fin, nouveau), {"field": op.field, "raw": texte[debut:fin]}


# ───────────────────────────── états ─────────────────────────────


def _references_d_etat(racine: MappingNode, nom: str) -> list[ScalarNode]:
    """Chaque scalaire qui NOMME l'état, hors sa clé dans `states`."""
    candidats: list[Node | None] = [_valeur(racine, "initial")]
    transitions = _valeur(racine, "transitions")
    for transition in transitions.value if isinstance(transitions, SequenceNode) else []:
        candidats += [_valeur(transition, champ) for champ in ("from", "to", "on_reject")]
        for reprise in ("on_fail", "on_changes_requested"):
            sous = _valeur(transition, reprise)
            candidats += [_valeur(sous, champ) for champ in ("to", "escalate_to")]
    defauts = _valeur(racine, "defaults")
    agents = _valeur(defauts, "from_any_agent_state")
    candidats += [_valeur(agents, champ) for champ in ("on_question", "on_budget_exceeded", "on_timeout")]
    candidats.append(_valeur(_valeur(defauts, "needs_human"), "on_abandon"))
    return [n for n in candidats if isinstance(n, ScalarNode) and n.value == nom]


def _a_effet(nom: str) -> list[str]:
    if nom.startswith(PREFIXES_A_EFFET):
        return [f"l'état `{nom}` porte un effet par son nom (ouverture de PR, fusion, mise en production)"]
    return []


def _etats(texte: str) -> tuple[MappingNode, _Collection]:
    racine = _composer(texte)
    return racine, _collection(texte, racine, "states")


def _add_state(texte: str, op: AddState) -> tuple[str, Any, list[str]]:
    _, etats = _etats(texte)
    if _cle(etats.noeud, op.name) is not None:
        raise EditionRefusee(f"l'état `{op.name}` existe déjà")
    place = etats.taille if op.index is None else op.index
    if op.text is not None:
        return etats.inserer_brut(place, op.text), RemoveState(name=op.name), []
    if not IDENTIFIANT.match(op.name):
        raise EditionRefusee(f"`{op.name}` n'est pas un nom d'état (minuscules, chiffres, `_`, `-`)")
    spec = {"display": op.name, **(op.spec or {})}
    return etats.inserer(place, f"{op.name}: {_rendre(spec)}"), RemoveState(name=op.name), _a_effet(op.name)


def _remove_state(texte: str, op: RemoveState) -> tuple[str, Any, list[str]]:
    racine, etats = _etats(texte)
    if _cle(etats.noeud, op.name) is None:
        raise EditionRefusee(f"l'état `{op.name}` n'existe pas")
    references = _references_d_etat(racine, op.name)
    if references:
        lignes = sorted({r.start_mark.line + 1 for r in references})
        raise EditionRefusee(
            f"l'état `{op.name}` est encore nommé ligne(s) {lignes} : retirez d'abord ses transitions"
        )
    place = _index_de_cle(etats.noeud, op.name)
    sans, retire = etats.retirer(place)
    return sans, AddState(name=op.name, text=retire, index=place), _a_effet(op.name)


def _rename_state(texte: str, op: RenameState) -> tuple[str, Any, list[str]]:
    racine, etats = _etats(texte)
    trouve = _cle(etats.noeud, op.from_)
    if trouve is None:
        raise EditionRefusee(f"l'état `{op.from_}` n'existe pas")
    if not IDENTIFIANT.match(op.to):
        raise EditionRefusee(f"`{op.to}` n'est pas un nom d'état (minuscules, chiffres, `_`, `-`)")
    if _cle(etats.noeud, op.to) is not None or _references_d_etat(racine, op.to):
        raise EditionRefusee(f"`{op.to}` est déjà un état, ou déjà nommé dans le workflow")
    noeuds = [trouve[0], *_references_d_etat(racine, op.from_)]
    # De la fin vers le début : chaque greffe garde valides les positions des précédentes.
    for noeud in sorted(noeuds, key=lambda n: -int(n.start_mark.index)):
        debut, fin = int(noeud.start_mark.index), int(noeud.end_mark.index)
        if noeud.style in {'"', "'"}:
            debut, fin = debut + 1, fin - 1
        texte = _greffer(texte, debut, fin, op.to)
    return texte, RenameState(**{"from": op.to, "to": op.from_}), _a_effet(op.from_) + _a_effet(op.to)


def _set_state(texte: str, op: SetState) -> tuple[str, Any, list[str]]:
    _, etats = _etats(texte)
    trouve = _cle(etats.noeud, op.name)
    if trouve is None:
        raise EditionRefusee(f"l'état `{op.name}` n'existe pas")
    nouveau, inverse = _modifier_cle(texte, _sous_collection(texte, trouve[0], trouve[1]), op)
    return nouveau, SetState(name=op.name, **inverse), []


# ───────────────────────────── transitions ─────────────────────────────


def _id_de(transition: Node) -> str | None:
    valeur = _valeur(transition, "id")
    return str(valeur.value) if isinstance(valeur, ScalarNode) else None


def _transitions(texte: str) -> _Collection:
    return _collection(texte, _composer(texte), "transitions")


def _transition(texte: str, ident: str) -> tuple[_Collection, int, Node]:
    transitions = _transitions(texte)
    for i, element in enumerate(transitions.noeud.value):
        if _id_de(element) == ident:
            return transitions, i, element
    raise EditionRefusee(f"la transition `{ident}` n'existe pas")


def _add_transition(texte: str, op: AddTransition) -> tuple[str, Any, list[str]]:
    transitions = _transitions(texte)
    place = transitions.taille if op.index is None else op.index
    if op.text is not None:
        avec = transitions.inserer_brut(place, op.text)
    else:
        spec = dict(op.transition or {})
        if not spec.get("from") or not spec.get("to"):
            raise EditionRefusee("une transition exige `from` et `to`")
        ident = str(spec.pop("id", None) or f"t-{spec['from']}-{spec['to']}")
        if ident in {_id_de(e) for e in transitions.noeud.value}:
            raise EditionRefusee(f"la transition `{ident}` existe déjà")
        rendu = _rendre({"id": ident, **spec})
        avec = transitions.inserer(place, rendu if transitions.en_flow else f"- {rendu}")
    ajoutee = _id_de(_transitions(avec).noeud.value[place])
    if ajoutee is None:
        raise EditionRefusee("une transition ajoutée doit porter son `id`")
    return avec, RemoveTransition(id=ajoutee), []


def _remove_transition(texte: str, op: RemoveTransition) -> tuple[str, Any, list[str]]:
    transitions, place, _ = _transition(texte, op.id)
    sans, retire = transitions.retirer(place)
    return sans, AddTransition(text=retire, index=place), []


def _set_transition(texte: str, op: SetTransition) -> tuple[str, Any, list[str]]:
    _, _, element = _transition(texte, op.id)
    if op.field == "id":
        raise EditionRefusee("l'`id` d'une transition ne se modifie pas : retirez-la, puis ajoutez-la")
    nouveau, inverse = _modifier_cle(texte, _element_de_sequence(texte, f"transition {op.id}", element), op)
    return nouveau, SetTransition(id=op.id, **inverse), []


# ───────────────────────────── garanties ─────────────────────────────


def _nom_de_garantie(noeud: Node) -> str | None:
    if isinstance(noeud, ScalarNode):
        return str(noeud.value)
    valeur = _valeur(noeud, "name")
    return str(valeur.value) if isinstance(valeur, ScalarNode) else None


def _add_gate(texte: str, op: AddGate) -> tuple[str, Any, list[str]]:
    _, _, element = _transition(texte, op.transition)
    trouve = _cle(element, "gates")
    if trouve is None:
        if op.gate is None:
            raise EditionRefusee("`gate` manquant")
        porteur = _element_de_sequence(texte, f"transition {op.transition}", element)
        nouveau, inverse = _modifier_cle(
            texte, porteur, SetTransition(id=op.transition, field="gates", value=[op.gate])
        )
        return nouveau, SetTransition(id=op.transition, **inverse), []
    garanties = _sous_collection(texte, trouve[0], trouve[1])
    place = garanties.taille if op.index is None else op.index
    if op.text is not None:
        avec = garanties.inserer_brut(place, op.text)
    elif op.gate is not None:
        nom = op.gate if isinstance(op.gate, str) else str(op.gate.get("name"))
        if nom in {_nom_de_garantie(g) for g in garanties.noeud.value}:
            raise EditionRefusee(f"la transition `{op.transition}` a déjà la garantie `{nom}`")
        rendu = _rendre(op.gate)
        avec = garanties.inserer(place, rendu if garanties.en_flow else f"- {rendu}")
    else:
        raise EditionRefusee("`gate` manquant")
    apres = _valeur(_transition(avec, op.transition)[2], "gates")
    assert isinstance(apres, SequenceNode)
    nom_ajoute = _nom_de_garantie(apres.value[place]) or ""
    return avec, RemoveGate(transition=op.transition, name=nom_ajoute, index=place), []


def _remove_gate(texte: str, op: RemoveGate) -> tuple[str, Any, list[str]]:
    _, _, element = _transition(texte, op.transition)
    trouve = _cle(element, "gates")
    if trouve is None or not isinstance(trouve[1], SequenceNode):
        raise EditionRefusee(f"la transition `{op.transition}` n'a pas de garanties")
    garanties = _sous_collection(texte, trouve[0], trouve[1])
    rangs = [r for r, g in enumerate(trouve[1].value) if _nom_de_garantie(g) == op.name]
    if op.index is not None:
        rangs = [r for r in rangs if r == op.index]
    if not rangs:
        raise EditionRefusee(f"la transition `{op.transition}` n'a pas la garantie `{op.name}`")
    sans, retire = garanties.retirer(rangs[0])
    return sans, AddGate(transition=op.transition, text=retire, index=rangs[0]), []


# ───────────────────────────── acteurs ─────────────────────────────


def _references_d_acteur(racine: MappingNode, nom: str) -> list[ScalarNode]:
    candidats: list[Node | None] = []
    transitions = _valeur(racine, "transitions")
    for transition in transitions.value if isinstance(transitions, SequenceNode) else []:
        candidats.append(_valeur(transition, "by"))
        candidats.append(_valeur(_valeur(transition, "train"), "approval"))
        agents = _valeur(_valeur(transition, "review"), "agents")
        candidats += list(agents.value) if isinstance(agents, SequenceNode) else []
    return [n for n in candidats if isinstance(n, ScalarNode) and n.value == nom]


def _acteurs(texte: str) -> tuple[MappingNode, _Collection]:
    racine = _composer(texte)
    return racine, _collection(texte, racine, "actors")


def _add_actor(texte: str, op: AddActor) -> tuple[str, Any, list[str]]:
    _, acteurs = _acteurs(texte)
    if _cle(acteurs.noeud, op.name) is not None:
        raise EditionRefusee(f"l'acteur `{op.name}` existe déjà")
    place = acteurs.taille if op.index is None else op.index
    if op.text is not None:
        return acteurs.inserer_brut(place, op.text), RemoveActor(name=op.name), []
    if not IDENTIFIANT.match(op.name):
        raise EditionRefusee(f"`{op.name}` n'est pas un nom d'acteur (minuscules, chiffres, `_`, `-`)")
    if not op.spec or "type" not in op.spec:
        raise EditionRefusee("un acteur exige son `type` : agent, human ou system")
    return acteurs.inserer(place, f"{op.name}: {_rendre(op.spec)}"), RemoveActor(name=op.name), []


def _remove_actor(texte: str, op: RemoveActor) -> tuple[str, Any, list[str]]:
    racine, acteurs = _acteurs(texte)
    if _cle(acteurs.noeud, op.name) is None:
        raise EditionRefusee(f"l'acteur `{op.name}` n'existe pas")
    references = _references_d_acteur(racine, op.name)
    if references:
        lignes = sorted({r.start_mark.line + 1 for r in references})
        raise EditionRefusee(f"l'acteur `{op.name}` porte encore des transitions, ligne(s) {lignes}")
    place = _index_de_cle(acteurs.noeud, op.name)
    sans, retire = acteurs.retirer(place)
    return sans, AddActor(name=op.name, text=retire, index=place), []


def _set_actor(texte: str, op: SetActor) -> tuple[str, Any, list[str]]:
    _, acteurs = _acteurs(texte)
    trouve = _cle(acteurs.noeud, op.name)
    if trouve is None:
        raise EditionRefusee(f"l'acteur `{op.name}` n'existe pas")
    nouveau, inverse = _modifier_cle(texte, _sous_collection(texte, trouve[0], trouve[1]), op)
    return nouveau, SetActor(name=op.name, **inverse), []


_APPLICATIONS: dict[str, Callable[[str, Any], tuple[str, Any, list[str]]]] = {
    "add_state": _add_state,
    "remove_state": _remove_state,
    "rename_state": _rename_state,
    "set_state": _set_state,
    "add_transition": _add_transition,
    "remove_transition": _remove_transition,
    "set_transition": _set_transition,
    "add_gate": _add_gate,
    "remove_gate": _remove_gate,
    "add_actor": _add_actor,
    "remove_actor": _remove_actor,
    "set_actor": _set_actor,
}

__all__ = ["OPERATIONS", "PREFIXES_A_EFFET", "Edition", "EditionRefusee", "Operation", "editer"]
