"""Ce qu'un connecteur métier promet, quel qu'il soit (S20-04) : la même suite juge chaque famille
(parc, transporteur, badges), servie typée ou en MCP, l'annuaire Entra et l'agent du fournisseur.

1. **Des schémas suivis** : chaque opération déclare un schéma d'entrée fermé, et l'implémentation
   accepte ce qu'il permet — toutes ses propriétés, ou les seules requises.
2. **Des écritures idempotentes** : une action gouvernée se rejoue (ADR 0035) ; une écriture
   rejouée ne change rien, une lecture n'écrit pas.
3. **Aucun secret au journal** : la clé du connecteur sert — une mauvaise est refusée — mais elle
   n'apparaît ni au journal, ni dans un résultat, ni dans un refus, ni dans la représentation du
   client.

Un vrai type (Intune, un transporteur, un fabricant de lecteurs) entrera ici avec le faux de son
API, et sera jugé pareil.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import structlog
from choregos_adapters.errors import AdapterError
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.conformance

SECRET = "cle-tres-secrete-0b7f"
MAUVAISE = "cle-de-quelqu-un-d-autre"
URL = "https://fournisseur.test/mcp"


@dataclass
class Operation:
    name: str
    access: str
    schema: dict[str, Any]


@dataclass
class Cas:
    operations: list[Operation]
    appeler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    etat: Callable[[], Any]
    client: Any
    #: des valeurs d'exemple propres à une opération, quand celles de la famille se contrediraient
    surcharges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def parametres(self, operation: Operation, *, tout: bool = True) -> dict[str, Any]:
        schema = operation.schema
        requis = set(schema.get("required", []))
        valeurs = {
            nom: _exemple(nom, sous_schema)
            for nom, sous_schema in schema.get("properties", {}).items()
            if tout or nom in requis
        }
        valeurs.update(
            {k: v for k, v in self.surcharges.get(operation.name, {}).items() if tout or k in requis}
        )
        return valeurs


def _exemple(nom: str, schema: dict[str, Any]) -> Any:
    """Une valeur tirée du NOM de la propriété : d'une opération à l'autre, `serial` ou `upn`
    désignent le même poste, le même compte — la suite joue une histoire cohérente."""
    match schema.get("type"):
        case "array":
            return [_exemple(nom, schema.get("items") or {"type": "string"})]
        case "integer":
            return 1
        case "boolean":
            return True
        case "object":
            return {}
        case _:
            return f"{nom}-1"


def _typees(famille: str) -> list[Operation]:
    from choregos_adapters import spec_of

    spec = spec_of(*famille.split("/"))
    assert spec is not None, f"{famille} n'est pas enregistré"
    return [Operation(o.name, o.access, dict(o.input_schema or {})) for o in spec.operations]


async def _typee(famille: str, cle: str, monkeypatch: pytest.MonkeyPatch) -> Cas:
    """Un connecteur `<famille>: demo`, construit par le registre, comme la plateforme le fait."""
    import choregos_adapters
    from choregos_adapters import build
    from choregos_adapters.fakes.rh import FAUX_PAR_FAMILLE

    faux = FAUX_PAR_FAMILLE[famille](cle_attendue=SECRET)
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, famille, faux)
    client = build(famille, "demo", {"api_key": cle})
    surcharges = {"create_return": {"reference": "reference-retour"}}
    return Cas(_typees(f"{famille}/demo"), client.executer, faux.etat, client, surcharges)


async def _en_mcp(faux: Any, cle: str) -> Cas:
    """Le même faux, servi en MCP : ses outils se découvrent, et le client les appelle."""
    from choregos_adapters.fakes.rh import serveur_mcp
    from choregos_adapters.mcp import ClientMcp

    client = ClientMcp(URL, token=cle, transport=serveur_mcp(faux, jeton=SECRET).transport())

    async def appeler(nom: str, params: dict[str, Any]) -> dict[str, Any]:
        resultat = await client.call_tool(nom, params)
        if resultat.get("isError"):
            raise AdapterError(str(resultat["content"][0]["text"]))
        return dict(resultat["structuredContent"])

    async def decouvrir() -> list[Operation]:
        return [
            Operation(o.name, "read" if o.read_only else "write", o.input_schema)
            for o in await client.list_tools()
        ]

    cas = Cas([], appeler, faux.etat, client, {"create_return": {"reference": "reference-retour"}})
    if cle == SECRET:
        cas.operations = await decouvrir()
    return cas


async def _entra(cle: str) -> Cas:
    from choregos_adapters.fakes.entra import FakeEntra
    from choregos_adapters.identity import EntraIdentity

    graph = FakeEntra(secret_attendu=SECRET)
    client = EntraIdentity(
        tenant_id="acme",
        client_id="choregos",
        client_secret=cle,
        administrative_unit_id=graph.unite,
        transport=graph.transport(),
    )

    def etat() -> Any:
        import copy

        return copy.deepcopy(
            {
                "comptes": graph.comptes,
                "groupes": {g: sorted(m) for g, m in graph.groupes.items()},
                "unite": sorted(graph.membres_de_l_unite),
                "revoquees": sorted(set(graph.sessions_revoquees)),
            }
        )

    return Cas(_typees("identity/entra"), client.executer, etat, client)


async def ouvrir(nom: str, monkeypatch: pytest.MonkeyPatch, cle: str = SECRET) -> Cas:
    from choregos_adapters.fakes.rh import FAUX_PAR_FAMILLE, FakeFournisseur

    forme, _, famille = nom.partition(":")
    if forme == "demo":
        return await _typee(famille, cle, monkeypatch)
    if forme == "mcp":
        faux = FakeFournisseur() if famille == "fournisseur" else FAUX_PAR_FAMILLE[famille]()
        return await _en_mcp(faux, cle)
    return await _entra(cle)


CAS = [
    "demo:mdm",
    "demo:shipping",
    "demo:access_control",
    "mcp:mdm",
    "mcp:shipping",
    "mcp:access_control",
    "mcp:fournisseur",
    "entra",
]


@pytest.mark.parametrize("nom", CAS)
async def test_chaque_operation_declare_un_schema_ferme_que_l_implementation_suit(
    nom: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = await ouvrir(nom, monkeypatch)
    assert cas.operations, f"{nom} n'annonce aucune opération"
    assert {o.access for o in cas.operations} == {"read", "write"}, "une famille lit et écrit"
    for operation in cas.operations:
        Draft202012Validator.check_schema(operation.schema)
        assert operation.schema.get("type") == "object"
        assert operation.schema.get("additionalProperties") is False, (
            f"{nom}/{operation.name} : un argument inattendu doit être refusé avant l'appel"
        )
        assert set(operation.schema.get("required", [])) <= set(operation.schema.get("properties", {}))
    for tout in (True, False):
        cas = await ouvrir(nom, monkeypatch)
        for operation in cas.operations:
            params = cas.parametres(operation, tout=tout)
            Draft202012Validator(operation.schema).validate(params)
            resultat = await cas.appeler(operation.name, params)
            assert isinstance(resultat, dict), f"{nom}/{operation.name} rend un objet"
            json.dumps(resultat)


#: Le type JSON de chaque annotation Python d'un paramètre.
TYPES_JSON = {str: "string", int: "integer", bool: "boolean", list: "array", dict: "object"}


def _type_json(annotation: Any) -> str:
    import types
    import typing

    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        (annotation,) = [a for a in typing.get_args(annotation) if a is not type(None)]
    return TYPES_JSON[typing.get_origin(annotation) or annotation]


@pytest.mark.parametrize("nom", ["demo:mdm", "demo:shipping", "demo:access_control", "entra"])
def test_le_schema_et_la_signature_disent_la_meme_chose(nom: str) -> None:
    """Ce que la famille déclare, l'implémentation le prend : les mêmes paramètres, requis quand ils
    n'ont pas de valeur par défaut, du même type."""
    import inspect
    import typing

    from choregos_adapters.fakes.rh import FAUX_PAR_FAMILLE
    from choregos_adapters.identity import EntraIdentity

    forme, _, famille = nom.partition(":")
    implementation: type[Any] = FAUX_PAR_FAMILLE[famille] if forme == "demo" else EntraIdentity
    for operation in _typees(f"{famille}/demo" if forme == "demo" else "identity/entra"):
        methode = getattr(implementation, operation.name)
        parametres = [
            p
            for p in inspect.signature(methode).parameters.values()
            if p.name != "self" and p.kind is not inspect.Parameter.VAR_KEYWORD
        ]
        types_python = typing.get_type_hints(methode)
        proprietes = operation.schema.get("properties", {})
        assert {p.name for p in parametres} == set(proprietes), f"{nom}/{operation.name}"
        assert {p.name for p in parametres if p.default is inspect.Parameter.empty} == set(
            operation.schema.get("required", [])
        ), f"{nom}/{operation.name} : requis d'un côté, facultatif de l'autre"
        for parametre in parametres:
            attendu = proprietes[parametre.name].get("type")
            assert _type_json(types_python[parametre.name]) == attendu, (
                f"{nom}/{operation.name}.{parametre.name}"
            )


@pytest.mark.parametrize("nom", CAS)
async def test_une_ecriture_rejouee_ne_change_rien_et_une_lecture_n_ecrit_pas(
    nom: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = await ouvrir(nom, monkeypatch)
    for operation in cas.operations:
        params = cas.parametres(operation)
        avant = cas.etat()
        await cas.appeler(operation.name, params)
        apres = cas.etat()
        if operation.access == "read":
            assert apres == avant, f"{nom}/{operation.name} est une lecture, et a écrit"
            # Ce qui n'existe pas se lit absent : sans erreur, et sans rien créer.
            inconnu = {k: f"{v}-inconnu" if isinstance(v, str) else v for k, v in params.items()}
            await cas.appeler(operation.name, inconnu)
            assert cas.etat() == apres, f"{nom}/{operation.name} a créé ce qu'on lui demandait de lire"
            continue
        assert apres != avant, f"{nom}/{operation.name} n'a rien écrit : son idempotence ne prouve rien"
        await cas.appeler(operation.name, params)
        assert cas.etat() == apres, f"{nom}/{operation.name}, rejouée, a changé l'état"


@pytest.mark.parametrize("nom", CAS)
async def test_la_cle_sert_et_n_apparait_nulle_part(
    nom: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as evenements:
        cas = await ouvrir(nom, monkeypatch)
        resultats: list[Any] = [await cas.appeler(o.name, cas.parametres(o)) for o in cas.operations]
        resultats.append(await cas.client.test())
        intrus = await ouvrir(nom, monkeypatch, cle=MAUVAISE)
        premiere = cas.operations[0]
        with pytest.raises(Exception) as refus:
            await intrus.appeler(premiere.name, intrus.parametres(premiere))
    code = getattr(refus.value, "status_code", None) or getattr(refus.value, "code", None)
    assert code == 401, f"{nom} : une mauvaise clé passe — la clé ne sert à rien, la taire ne prouve rien"
    traces = [
        *(r.getMessage() for r in caplog.records),
        *(repr(e) for e in evenements),
        *(json.dumps(r, default=str) for r in resultats),
        str(refus.value),
        repr(cas.client),
        repr(intrus.client),
    ]
    fuites = [t for t in traces if SECRET in t or MAUVAISE in t]
    assert fuites == [], f"{nom} : une clé se lit dans {fuites[:3]}"
