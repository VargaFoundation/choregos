"""Les exemples doivent valider à la fois contre les JSON Schemas et contre les modèles Python.

C'est ce test qui empêche la dérive entre `schemas/*.json`, `openapi.yaml` et `choregos_contracts.*`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import (
    ContextPack,
    Finding,
    HumanDecision,
    InboundEvent,
    Policy,
    ProjectConfig,
    StageInput,
    StageResult,
    Workflow,
)
from jsonschema import Draft202012Validator, RefResolver

SCHEMAS = contracts.schemas_dir()
EXAMPLES = SCHEMAS / "examples"


def _validator(schema_name: str) -> Draft202012Validator:
    schema = contracts.load_schema(schema_name)
    store = {}
    for name in contracts.SCHEMA_FILES:
        sub = contracts.load_schema(name)
        store[sub["$id"]] = sub
        store[name] = sub
    resolver = RefResolver(base_uri=schema["$id"], referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver)


def _load(example: str) -> Any:
    with (EXAMPLES / example).open(encoding="utf-8") as fh:
        return json.load(fh)


def test_all_declared_schemas_exist() -> None:
    for name in contracts.SCHEMA_FILES:
        assert (SCHEMAS / name).exists(), name
    on_disk = {p.name for p in SCHEMAS.glob("*.json")}
    assert on_disk == set(contracts.SCHEMA_FILES), "SCHEMA_FILES et schemas/ ont divergé"


@pytest.mark.parametrize(
    ("example", "schema_name"),
    [
        ("workflow.default-simple.json", "workflow.schema.json"),
        ("policy.solo.json", "policy.schema.json"),
        ("project.example.json", "project.schema.json"),
        ("stage-input.example.json", "stage-input.schema.json"),
        ("stage-result.example.json", "stage-result.schema.json"),
        ("finding.example.json", "finding.schema.json"),
        ("inbound-event.example.json", "inbound-event.schema.json"),
        ("human-decision.example.json", "human-decision.schema.json"),
        ("context-pack.example.json", "context-pack.schema.json"),
        ("event.example.json", "event.schema.json"),
    ],
)
def test_example_matches_json_schema(example: str, schema_name: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(_load(example)), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize(
    ("example", "model"),
    [
        ("workflow.default-simple.json", Workflow),
        ("policy.solo.json", Policy),
        ("project.example.json", ProjectConfig),
        ("stage-input.example.json", StageInput),
        ("stage-result.example.json", StageResult),
        ("finding.example.json", Finding),
        ("inbound-event.example.json", InboundEvent),
        ("human-decision.example.json", HumanDecision),
        ("context-pack.example.json", ContextPack),
    ],
)
def test_example_matches_python_model(example: str, model: type) -> None:
    raw = _load(example)
    obj = model.model_validate(raw)
    dumped = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    # Le modèle ne doit perdre aucune clé renseignée de l'exemple.
    missing = {k for k, v in raw.items() if v is not None} - set(dumped)
    assert not missing, f"clés perdues par {model.__name__} : {missing}"


def _modele_imbrique(annotation: Any) -> type | None:
    """Le modèle Pydantic caché derrière `X | None`, `list[X]`, `dict[str, X]` — sinon None."""
    import typing

    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        trouve = _modele_imbrique(arg)
        if trouve is not None:
            return trouve
    return None


def _champs_du_schema(noeud: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Les propriétés d'un nœud objet, en suivant `anyOf`/`oneOf` (un champ nullable)."""
    if "properties" in noeud:
        return dict(noeud["properties"])
    for combinaison in ("anyOf", "oneOf", "allOf"):
        for branche in noeud.get(combinaison, []):
            if isinstance(branche, dict) and "properties" in branche:
                return dict(branche["properties"])
    return {}


def _comparer(chemin: str, noeud: dict[str, Any], model: type, ecarts: list[str]) -> None:
    schema_champs = _champs_du_schema(noeud)
    if not schema_champs:
        return
    modele_champs = {
        (info.alias or nom): info
        for nom, info in model.model_fields.items()  # type: ignore[attr-defined]
    }
    absents = set(modele_champs) - set(schema_champs)
    en_trop = set(schema_champs) - set(modele_champs)
    for nom in sorted(absents):
        ecarts.append(f"{chemin}.{nom} : dans le modèle {model.__name__}, absent du schéma")
    for nom in sorted(en_trop):
        ecarts.append(f"{chemin}.{nom} : dans le schéma, absent du modèle {model.__name__}")
    for nom in sorted(set(schema_champs) & set(modele_champs)):
        sous_modele = _modele_imbrique(modele_champs[nom].annotation)
        if sous_modele is not None:
            _comparer(f"{chemin}.{nom}", schema_champs[nom], sous_modele, ecarts)


@pytest.mark.parametrize(
    ("schema_name", "model"),
    [
        ("stage-result.schema.json", StageResult),
        ("stage-input.schema.json", StageInput),
        ("finding.schema.json", Finding),
        ("human-decision.schema.json", HumanDecision),
    ],
)
def test_le_schema_et_le_modele_declarent_les_memes_champs(schema_name: str, model: type) -> None:
    """Les exemples ne suffisent pas à empêcher la dérive : ils n'exercent que les champs
    qu'ils portent. Un champ ajouté au modèle Python et oublié dans le schéma passait donc
    tous les contrôles — arrivé le 2026-09-24 avec `Evidence.facts`, que le schéma rejetait
    (`additionalProperties: false`) alors que le modèle l'acceptait. Ici on compare les
    déclarations, pas un exemple."""
    ecarts: list[str] = []
    _comparer(schema_name.split(".", maxsplit=1)[0], contracts.load_schema(schema_name), model, ecarts)
    assert not ecarts, "\n".join(ecarts)


def test_openapi_is_coherent() -> None:
    spec = contracts.load_openapi()
    assert spec["openapi"].startswith("3.1")
    operations = [
        op["operationId"]
        for path in spec["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == len(set(operations)), "operationId dupliqué"
    # Toute référence interne pointe vers un composant existant.
    text = json.dumps(spec)
    for ref in {r for r in _iter_refs(spec) if r.startswith("#/")}:
        node: Any = spec
        for part in ref.removeprefix("#/").split("/"):
            assert part in node, f"référence cassée : {ref}"
            node = node[part]
    assert "choregos" in text


def test_openapi_external_refs_exist() -> None:
    spec = contracts.load_openapi()
    for ref in {r for r in _iter_refs(spec) if r.startswith("./")}:
        target = Path(contracts.contracts_dir() / ref.removeprefix("./"))
        assert target.exists(), f"schéma référencé manquant : {ref}"


def _iter_refs(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_iter_refs(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_iter_refs(item))
    return out


def test_stage_result_helpers() -> None:
    result = StageResult.failure("agent injoignable", reason="agent_error")
    assert not result.ok
    assert result.status == "failed"
    assert ContextPack.empty("q").is_empty()
