"""What a business connector promises, whatever it is (S20-04): the same suite judges each family
(fleet, carrier, badges), served typed or over MCP, the Entra directory and the supplier's agent.

1. **Schemas followed**: each operation declares a closed input schema, and the implementation
   accepts what it allows — all its properties, or only the required ones.
2. **Idempotent writes**: a governed action is replayed (ADR 0035); a replayed write changes
   nothing, a read does not write.
3. **No secret in the log**: the connector's key works — a wrong one is refused — but it
   appears neither in the log, nor in a result, nor in a refusal, nor in the client's
   representation.

A real type (Intune, a carrier, a reader manufacturer) will come in here with the fake of its
API, and will be judged the same.
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


@pytest.fixture(autouse=True)
def _egress_of_the_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake hosts (`fournisseur.test`, `demo`) carry no egress rule: this module sets its own mode
    instead of inheriting `warn` from a `conftest.py` another suite imported first (#771)."""
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "warn")


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
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    state: Callable[[], Any]
    client: Any
    #: example values specific to one operation, when the family's would contradict each other
    surcharges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def params(self, operation: Operation, *, everything: bool = True) -> dict[str, Any]:
        schema = operation.schema
        requis = set(schema.get("required", []))
        values = {
            name: _example(name, sous_schema)
            for name, sous_schema in schema.get("properties", {}).items()
            if everything or name in requis
        }
        values.update(
            {k: v for k, v in self.surcharges.get(operation.name, {}).items() if everything or k in requis}
        )
        return values


def _example(name: str, schema: dict[str, Any]) -> Any:
    """A value drawn from the property NAME: from one operation to the next, `serial` or `upn`
    designate the same workstation, the same account — the suite plays a coherent story."""
    match schema.get("type"):
        case "array":
            return [_example(name, schema.get("items") or {"type": "string"})]
        case "integer":
            return 1
        case "boolean":
            return True
        case "object":
            return {}
        case _:
            return f"{name}-1"


def _typees(famille: str) -> list[Operation]:
    from choregos_adapters import spec_of

    spec = spec_of(*famille.split("/"))
    assert spec is not None, f"{famille} is not registered"
    return [Operation(o.name, o.access, dict(o.input_schema or {})) for o in spec.operations]


async def _typee(famille: str, key: str, monkeypatch: pytest.MonkeyPatch) -> Cas:
    """A `<family>: demo` connector, built by the registry, as the platform does."""
    import choregos_adapters
    from choregos_adapters import build
    from choregos_adapters.fakes.hr import FAKES_BY_FAMILY

    fake = FAKES_BY_FAMILY[famille](expected_key=SECRET)
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, famille, fake)
    client = build(famille, "demo", {"api_key": key})
    surcharges = {"create_return": {"reference": "reference-retour"}}
    return Cas(_typees(f"{famille}/demo"), client.execute, fake.state, client, surcharges)


async def _en_mcp(fake: Any, key: str) -> Cas:
    """The same fake, served over MCP: its tools are discovered, and the client calls them."""
    from choregos_adapters.fakes.hr import mcp_server
    from choregos_adapters.mcp import McpClient

    client = McpClient(URL, token=key, transport=mcp_server(fake, token=SECRET).transport())

    async def call(name: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await client.call_tool(name, params)
        if result.get("isError"):
            raise AdapterError(str(result["content"][0]["text"]))
        return dict(result["structuredContent"])

    async def decouvrir() -> list[Operation]:
        return [
            Operation(o.name, "read" if o.read_only else "write", o.input_schema)
            for o in await client.list_tools()
        ]

    cas = Cas([], call, fake.state, client, {"create_return": {"reference": "reference-retour"}})
    if key == SECRET:
        cas.operations = await decouvrir()
    return cas


async def _servie(famille: str, key: str) -> Cas:
    """A `demo` connector that carries a `url`: the fake served by ONE process (`fakes/serveur.py`),
    what the dev deploys — its operations are the family's, declared, not discovered."""
    import httpx
    from choregos_adapters.fakes.hr import RemoteDesk
    from choregos_adapters.fakes.serveur import DemoApplication
    from choregos_adapters.mcp import McpClient

    app = DemoApplication(token=SECRET)
    client = RemoteDesk(
        McpClient(f"http://demo/{famille}/mcp", token=key, transport=httpx.ASGITransport(app=app))
    )
    surcharges = {"create_return": {"reference": "reference-retour"}}
    return Cas(_typees(f"{famille}/demo"), client.execute, app.fakes[famille].state, client, surcharges)


async def _entra(key: str) -> Cas:
    from choregos_adapters.fakes.entra import FakeEntra
    from choregos_adapters.identity import EntraIdentity

    graph = FakeEntra(expected_secret=SECRET)
    client = EntraIdentity(
        tenant_id="acme",
        client_id="choregos",
        client_secret=key,
        administrative_unit_id=graph.unit,
        transport=graph.transport(),
    )

    def state() -> Any:
        import copy

        return copy.deepcopy(
            {
                "comptes": graph.accounts,
                "groupes": {g: sorted(m) for g, m in graph.groups.items()},
                "unite": sorted(graph.unit_members),
                "revoquees": sorted(set(graph.revoked_sessions)),
            }
        )

    return Cas(_typees("identity/entra"), client.execute, state, client)


async def ouvrir(name: str, monkeypatch: pytest.MonkeyPatch, key: str = SECRET) -> Cas:
    from choregos_adapters.fakes.hr import FAKES_BY_FAMILY, FakeSupplier

    forme, _, famille = name.partition(":")
    if forme == "demo":
        return await _typee(famille, key, monkeypatch)
    if forme == "mcp":
        fake = FakeSupplier() if famille == "fournisseur" else FAKES_BY_FAMILY[famille]()
        return await _en_mcp(fake, key)
    if forme == "servi":
        return await _servie(famille, key)
    return await _entra(key)


CAS = [
    "demo:mdm",
    "demo:shipping",
    "demo:access_control",
    "mcp:mdm",
    "mcp:shipping",
    "mcp:access_control",
    "mcp:fournisseur",
    "servi:mdm",
    "servi:shipping",
    "servi:access_control",
    "entra",
]


@pytest.mark.parametrize("name", CAS)
async def test_each_operation_declares_a_closed_schema_the_implementation_follows(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = await ouvrir(name, monkeypatch)
    assert cas.operations, f"{name} announces no operation"
    assert {o.access for o in cas.operations} == {"read", "write"}, "a family reads and writes"
    for operation in cas.operations:
        Draft202012Validator.check_schema(operation.schema)
        assert operation.schema.get("type") == "object"
        assert operation.schema.get("additionalProperties") is False, (
            f"{name}/{operation.name}: an unexpected argument must be refused before the call"
        )
        assert set(operation.schema.get("required", [])) <= set(operation.schema.get("properties", {}))
    for everything in (True, False):
        cas = await ouvrir(name, monkeypatch)
        for operation in cas.operations:
            params = cas.params(operation, everything=everything)
            Draft202012Validator(operation.schema).validate(params)
            result = await cas.call(operation.name, params)
            assert isinstance(result, dict), f"{name}/{operation.name} returns an object"
            json.dumps(result)


#: The JSON type of each Python annotation of a parameter.
TYPES_JSON = {str: "string", int: "integer", bool: "boolean", list: "array", dict: "object"}


def _type_json(annotation: Any) -> str:
    import types
    import typing

    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        (annotation,) = [a for a in typing.get_args(annotation) if a is not type(None)]
    return TYPES_JSON[typing.get_origin(annotation) or annotation]


@pytest.mark.parametrize("name", ["demo:mdm", "demo:shipping", "demo:access_control", "entra"])
def test_the_schema_and_the_signature_say_the_same_thing(name: str) -> None:
    """What the family declares, the implementation takes: the same parameters, required when they
    have no default value, of the same type."""
    import inspect
    import typing

    from choregos_adapters.fakes.hr import FAKES_BY_FAMILY
    from choregos_adapters.identity import EntraIdentity

    forme, _, famille = name.partition(":")
    implementation: type[Any] = FAKES_BY_FAMILY[famille] if forme == "demo" else EntraIdentity
    for operation in _typees(f"{famille}/demo" if forme == "demo" else "identity/entra"):
        methode = getattr(implementation, operation.name)
        params = [
            p
            for p in inspect.signature(methode).parameters.values()
            if p.name != "self" and p.kind is not inspect.Parameter.VAR_KEYWORD
        ]
        types_python = typing.get_type_hints(methode)
        proprietes = operation.schema.get("properties", {})
        assert {p.name for p in params} == set(proprietes), f"{name}/{operation.name}"
        assert {p.name for p in params if p.default is inspect.Parameter.empty} == set(
            operation.schema.get("required", [])
        ), f"{name}/{operation.name}: required on one side, optional on the other"
        for parametre in params:
            expected = proprietes[parametre.name].get("type")
            assert _type_json(types_python[parametre.name]) == expected, (
                f"{name}/{operation.name}.{parametre.name}"
            )


@pytest.mark.parametrize("name", CAS)
async def test_a_replayed_write_changes_nothing_and_a_read_writes_nothing(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cas = await ouvrir(name, monkeypatch)
    for operation in cas.operations:
        params = cas.params(operation)
        avant = cas.state()
        await cas.call(operation.name, params)
        after = cas.state()
        if operation.access == "read":
            assert after == avant, f"{name}/{operation.name} is a read, and wrote"
            # What does not exist reads as absent: no error, and nothing created.
            inconnu = {k: f"{v}-inconnu" if isinstance(v, str) else v for k, v in params.items()}
            await cas.call(operation.name, inconnu)
            assert cas.state() == after, f"{name}/{operation.name} created what it was asked to read"
            continue
        assert after != avant, f"{name}/{operation.name} wrote nothing: its idempotence proves nothing"
        await cas.call(operation.name, params)
        assert cas.state() == after, f"{name}/{operation.name}, replayed, changed the state"


@pytest.mark.parametrize("name", CAS)
async def test_the_key_is_used_and_appears_nowhere(
    name: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        cas = await ouvrir(name, monkeypatch)
        results: list[Any] = [await cas.call(o.name, cas.params(o)) for o in cas.operations]
        results.append(await cas.client.test())
        intrus = await ouvrir(name, monkeypatch, key=MAUVAISE)
        premiere = cas.operations[0]
        with pytest.raises(Exception) as refusal:
            await intrus.call(premiere.name, intrus.params(premiere))
    code = getattr(refusal.value, "status_code", None) or getattr(refusal.value, "code", None)
    assert code == 401, f"{name}: a wrong key gets through — the key is useless, hiding it proves nothing"
    traces = [
        *(r.getMessage() for r in caplog.records),
        *(repr(e) for e in events),
        *(json.dumps(r, default=str) for r in results),
        str(refusal.value),
        repr(cas.client),
        repr(intrus.client),
    ]
    fuites = [t for t in traces if SECRET in t or MAUVAISE in t]
    assert fuites == [], f"{name}: a key can be read in {fuites[:3]}"
