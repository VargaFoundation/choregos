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
    ContextPolicy,
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
        ("workflow.onboarding-actions.json", "workflow.schema.json"),
        ("policy.solo.json", "policy.schema.json"),
        ("project.example.json", "project.schema.json"),
        ("stage-input.example.json", "stage-input.schema.json"),
        ("stage-input.code-agent.example.json", "stage-input.schema.json"),
        ("stage-input.remote.example.json", "stage-input.schema.json"),
        ("stage-input.hosted.example.json", "stage-input.schema.json"),
        ("stage-input.prompt.example.json", "stage-input.schema.json"),
        ("stage-result.example.json", "stage-result.schema.json"),
        ("finding.example.json", "finding.schema.json"),
        ("inbound-event.example.json", "inbound-event.schema.json"),
        ("human-decision.example.json", "human-decision.schema.json"),
        ("context-pack.example.json", "context-pack.schema.json"),
        ("event.example.json", "event.schema.json"),
        ("ui-manifest.example.json", "ui-manifest.schema.json"),
    ],
)
def test_example_matches_json_schema(example: str, schema_name: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(_load(example)), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize(
    ("example", "model"),
    [
        ("workflow.default-simple.json", Workflow),
        ("workflow.onboarding-actions.json", Workflow),
        ("policy.solo.json", Policy),
        ("project.example.json", ProjectConfig),
        ("stage-input.example.json", StageInput),
        ("stage-input.code-agent.example.json", StageInput),
        ("stage-input.remote.example.json", StageInput),
        ("stage-input.hosted.example.json", StageInput),
        ("stage-input.prompt.example.json", StageInput),
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
        ("policy.schema.json", Policy),
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


def test_le_schema_des_evenements_connait_chaque_type_emis() -> None:
    """`EventType` est ce que la plateforme ÉMET ; le schéma, ce qu'un consommateur accepte.

    Ils avaient divergé sans que rien ne rougisse : `workflow_failed` et `tool.called` étaient
    émis, et un consommateur validant contre le schéma les rejetait.
    """
    from choregos_contracts.enums import EventType

    declares = set(contracts.load_schema("event.schema.json")["properties"]["type"]["enum"])
    assert {t.value for t in EventType} == declares


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


# ───────────────────────── context reduction (S37-02, ADR 0055) ─────────────────────────


def test_a_policy_without_context_reduces_nothing() -> None:
    policy = Policy.model_validate(_load("policy.solo.json"))
    assert "context" not in _load("policy.solo.json")
    for role in ("implement", "review", "triage"):
        assert not policy.context.for_role(role).active
    stage_input = StageInput.model_validate(_load("stage-input.example.json"))
    assert not stage_input.context.active


def test_a_role_override_changes_that_role_only() -> None:
    raw = _load("policy.solo.json") | {
        "context": {
            "shell_filter": "rtk",
            "compression": "headroom",
            "headroom_profile": "balanced",
            "on_unavailable": "fail",
            "rtk_exclude_commands": ["git diff"],
            "per_role": {"review": {"compression": "none"}},
        }
    }
    assert not list(_validator("policy.schema.json").iter_errors(raw))
    context = Policy.model_validate(raw).context
    implement, review = context.for_role("implement"), context.for_role("review")
    assert (implement.shell_filter, implement.compression) == ("rtk", "headroom")
    assert (review.shell_filter, review.compression) == ("rtk", "none")
    assert review.headroom_profile == "balanced" and review.on_unavailable == "fail"
    assert review.rtk_exclude_commands == ["git diff"]


def test_the_context_schema_and_model_declare_the_same_fields() -> None:
    """The `context` block, overrides included; the whole policy is compared above."""
    gaps: list[str] = []
    node = contracts.load_schema("policy.schema.json")["properties"]["context"]
    _comparer("policy.context", node, ContextPolicy, gaps)
    assert not gaps, "\n".join(gaps)


def test_an_unknown_reduction_is_refused_by_the_schema_and_the_model() -> None:
    raw = _load("policy.solo.json") | {"context": {"compression": "zip"}}
    assert list(_validator("policy.schema.json").iter_errors(raw))
    with pytest.raises(ValueError, match="compression"):
        Policy.model_validate(raw)


def _edition_validator() -> Draft202012Validator:
    spec = contracts.load_openapi()
    schema = spec["paths"]["/edition"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return Draft202012Validator(schema)


def test_the_edition_schema_accepts_the_community_and_the_enterprise_shapes() -> None:
    validator = _edition_validator()
    base = {"edition": "community", "features": [], "version": "0.17.16"}
    validator.validate(base)  # an older core, without the two new fields, stays valid
    validator.validate({**base, "publisher": None, "edition_version": None})
    validator.validate({**base, "edition": "enterprise", "publisher": "Acme", "edition_version": "0.2.0"})
    # the about panel's fields: all optional, so an older core stays valid
    validator.validate(
        {**base, "licence": "Apache-2.0", "edition_licence": None, "build": {"revision": None}}
    )
    validator.validate(
        {
            **base,
            "edition": "enterprise",
            "licence": "Apache-2.0",
            "edition_licence": "Acme Corp EULA",
            "build": {"revision": "0123abc"},
        }
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"publisher": 3},
        {"edition_version": 3},
        {"publisher": "x" * 81},
        {"edition_licence": 3},
        {"edition_licence": "x" * 81},
        {"licence": 3},
        {"build": {}},
        {"build": {"revision": "NOT-A-SHA"}},
        {"build": {"revision": "abc"}},
    ],
)
def test_the_edition_schema_refuses_a_malformed_publisher_or_version(extra: dict[str, Any]) -> None:
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        _edition_validator().validate({"edition": "community", "features": [], "version": "1", **extra})


def test_stage_input_carries_the_resolved_memories() -> None:
    """S40-11: the example holds two memories, the model reads them, the schema refuses unknown fields."""
    example = _load("stage-input.example.json")
    assert [m["label"] for m in example["memories"]] == ["global:security-lessons", "project:shared"]
    parsed = contracts.StageInput.model_validate(example)
    assert [(m.label, m.mode) for m in parsed.memories] == [
        ("global:security-lessons", "read"),
        ("project:shared", "write"),
    ]
    bad = {**example, "memories": [{**example["memories"][0], "surprise": 1}]}
    assert list(_validator("stage-input.schema.json").iter_errors(bad))
    # Before S40-11 a stage input had none: the field defaults to an empty list.
    legacy = {k: v for k, v in example.items() if k != "memories"}
    assert contracts.StageInput.model_validate(legacy).memories == []


def test_a_context_pack_item_may_name_its_memory() -> None:
    item = contracts.MemoryItem(kind="decision", subject="s", content="c", memory="global:lessons")
    assert item.memory == "global:lessons"
    assert contracts.MemoryItem(kind="decision", subject="s", content="c").memory is None
    pack = _load("context-pack.example.json")
    pack["memories"][0]["memory"] = "global:lessons"
    assert not list(_validator("context-pack.schema.json").iter_errors(pack))


def test_the_network_fields_exist_in_the_models_and_the_schemas_with_the_same_defaults() -> None:
    """ADR 0070: `NetworkPolicy` grew a deny list, rates and a built-in web tools switch; `Permissions`
    carries them to the runner. A default that differs between model and schema would make the two
    disagree on what an old file means."""
    from choregos_contracts import Permissions
    from choregos_contracts.policy import NetworkPolicy

    policy_props = contracts.load_schema("policy.schema.json")["properties"]["sandbox"]["properties"][
        "network"
    ]["properties"]
    assert set(policy_props) == set(NetworkPolicy.model_fields)
    defaults = NetworkPolicy()
    assert defaults.deny_domains == [] and defaults.builtin_web_tools == "allow"
    assert defaults.per_host_per_minute is None and defaults.per_run_per_minute is None
    assert policy_props["builtin_web_tools"]["default"] == defaults.builtin_web_tools

    perms = contracts.load_schema("stage-input.schema.json")["properties"]["permissions"]["properties"]
    for name in ("deny_domains", "egress", "builtin_web_tools"):
        assert name in perms and name in Permissions.model_fields
    fresh = Permissions()
    assert fresh.egress == "unlisted" and fresh.builtin_web_tools is True and fresh.deny_domains == []
    assert perms["egress"]["default"] == "unlisted" and perms["builtin_web_tools"]["default"] is True


def test_the_policy_and_a_stage_input_with_the_new_network_keys_validate_against_the_schemas() -> None:
    policy = _load("policy.solo.json")
    policy["sandbox"]["network"].update(
        {"deny_domains": ["x.example"], "per_host_per_minute": 30, "builtin_web_tools": "deny"}
    )
    assert not list(_validator("policy.schema.json").iter_errors(policy))
    Policy.model_validate(policy)
    policy["sandbox"]["network"]["per_host_per_minute"] = 0
    assert list(_validator("policy.schema.json").iter_errors(policy))

    stage = _load("stage-input.example.json")
    stage["permissions"].update(
        {"deny_domains": ["x.example"], "egress": "listed", "builtin_web_tools": False}
    )
    assert not list(_validator("stage-input.schema.json").iter_errors(stage))
    StageInput.model_validate(stage)


def test_the_network_denied_event_is_declared_and_the_openapi_names_the_four_operations() -> None:
    from choregos_contracts.enums import EventType

    assert EventType.NETWORK_DENIED.value == "choregos.network.denied"
    assert (
        "choregos.network.denied" in contracts.load_schema("event.schema.json")["properties"]["type"]["enum"]
    )
    spec = contracts.load_openapi()
    operations = {
        op["operationId"]
        for path in spec["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post"}
    }
    assert {
        "getRunNetwork",
        "postRunNetworkDecisions",
        "getProjectNetwork",
        "getNetworkActivity",
    } <= operations
    agent = spec["components"]["schemas"]["AgentSpec"]["properties"]["network"]
    assert agent == {"$ref": "#/components/schemas/AgentNetwork"}


def test_admin_section_block_permission_is_optional_and_a_string() -> None:
    """S42-02: every block kind may carry its own permission; a non-string is refused."""
    validator = _validator("ui-manifest.schema.json")
    section = _load("ui-manifest.example.json")
    assert any("permission" in block for block in section["blocks"])
    assert not list(validator.iter_errors(section))
    for block in section["blocks"]:
        broken = {**section, "blocks": [{**block, "permission": 7}]}
        assert list(validator.iter_errors(broken)), block["kind"]
    bare = {
        **section,
        "blocks": [{k: v for k, v in b.items() if k != "permission"} for b in section["blocks"]],
    }
    assert not list(validator.iter_errors(bare))


def test_an_asset_property_names_where_its_file_goes() -> None:
    """S42-13: `format: asset` needs an `x-upload` path; accept and size limits are typed."""
    validator = _validator("ui-manifest.schema.json")
    section = _load("ui-manifest.example.json")
    form = section["blocks"][0]
    asset = form["schema"]["properties"]["badge"]
    assert asset["format"] == "asset"
    assert not list(validator.iter_errors(section))

    def with_badge(badge: dict) -> dict:
        schema = {**form["schema"], "properties": {"badge": badge}}
        return {**section, "blocks": [{**form, "schema": schema}]}

    without_upload = {k: v for k, v in asset.items() if k != "x-upload"}
    assert list(validator.iter_errors(with_badge(without_upload)))
    assert list(validator.iter_errors(with_badge({**asset, "x-upload": "https://elsewhere/up"})))
    assert list(validator.iter_errors(with_badge({**asset, "x-max-bytes": 0})))
    assert list(validator.iter_errors(with_badge({**asset, "x-accept": ["png"]})))
    assert list(validator.iter_errors(with_badge({**asset, "type": "object"})))
    # A plain string property is untouched.
    assert not list(validator.iter_errors(with_badge({"type": "string"})))


def test_the_web_search_schema_and_model_declare_the_same_fields() -> None:
    """ADR 0083: the project's narrowing of web_search, in the schema as in the model."""
    from choregos_contracts.policy import WebSearchPolicy

    gaps: list[str] = []
    node = contracts.load_schema("policy.schema.json")["properties"]["web_search"]
    _comparer("policy.web_search", node, WebSearchPolicy, gaps)
    assert not gaps, "\n".join(gaps)
    raw = _load("policy.solo.json") | {"web_search": {"allow_query_reuse": "maybe"}}
    assert list(_validator("policy.schema.json").iter_errors(raw))
    with pytest.raises(ValueError, match="allow_query_reuse"):
        Policy.model_validate(raw)


def test_the_run_access_record_carries_an_optional_chain() -> None:
    """S40-22 (ADR 0077 §2): the chain is optional, and its initiator admits any `<kind>:<subject>`."""
    spec = contracts.load_openapi()
    access = spec["paths"]["/runs/{id}/access"]["get"]["responses"]["200"]["content"]["application/json"]
    schema = access["schema"]
    assert "chain" in schema["properties"] and "chain" not in schema["required"]
    validator = Draft202012Validator(schema)
    record: dict[str, Any] = {"evenements": 0, "refusals": 0, "acces": []}
    assert not list(validator.iter_errors(record)), "a run recorded before S40-22 stays valid"
    for initiator in ("person:ada@example.org", "a2a:rovo", "schedule:nightly", None):
        chain = {
            "initiated_by": initiator,
            "runs": [
                {
                    "run_id": "T-1-plan-1",
                    "stage": "plan",
                    "agent_slug": "planner",
                    "agent_version": 3,
                    "inputs_from": [],
                },
                {
                    "run_id": "T-1-implement-1",
                    "stage": "implement",
                    "agent_slug": None,
                    "agent_version": None,
                    "inputs_from": ["T-1-plan-1"],
                },
            ],
        }
        assert not list(validator.iter_errors({**record, "chain": chain})), initiator
    broken = {**record, "chain": {"initiated_by": "person:ada@example.org", "runs": [{"run_id": "x"}]}}
    assert list(validator.iter_errors(broken)), "a run of the chain names its stage and its inputs"
