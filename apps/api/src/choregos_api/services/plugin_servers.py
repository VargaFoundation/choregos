# SPDX-License-Identifier: Apache-2.0
"""A plugin's servers: run by the platform, governed as organisation connectors (ADR 0096, S43-06).

A plugin brings code only as an MCP server image. When the plugin is installed (after the second
approval, ADR 0050), each of its servers becomes:

- an **organisation `mcp` connector named after the server** (`servicenow`), so the agent's tools are
  `<server>__<operation>`. A connector of that name the plugin does not own is a 409 before anything
  is written;
- one **`ConnectorOperation` per declared operation**, at the floor of its declaration: a read is
  allowed, a write needs an approval, and `human_approval` is never lowered (`connecteurs.human_floor`
  reads the declaration kept on the connector, not the row). A tool the server serves that its
  `operations` do not declare is refused: discovery does not add it, the broker does not offer it, a
  governed action does not play it;
- **objects in the plugins namespace** (`choregos_adapters.plugin_servers`): the Deployment, its
  Service, its NetworkPolicy, and a Secret with the server's configuration and the bearer token the
  platform generated for it, sealed in the organisation's store (ADR 0058) under the connector's
  `_server_token` field and presented on every broker call (`CHOREGOS_SERVER_TOKEN` in the pod).

The server's `config_schema` and `secret_fields` are the connector's configuration: an administrator
types them with the connector routes (secrets sealed); every change reconciles the objects, and the
digest of the Secret on the pod template rolls the Deployment. Until the configuration satisfies its
schema the Deployment has no replica and the connector says `unconfigured`.

The image follows the hosted-agent rules (ADR 0080 §8): pinned by digest (the contract), under
`CHOREGOS_PLUGIN_SERVERS_REGISTRIES`, and signed per `CHOREGOS_HOSTED_COSIGN`.

**The eval instance (#1372).** A server that declares its `fake` (the variables that switch it to its
seeded fake, the scenes that fake serves) is also run, where the deployment serves eval fakes
(`CHOREGOS_EVAL_FAKE_SCENES`) and only there, as a second set of objects (`fake_object`): the same image,
the fake's variables and the platform's token, no configuration, no egress at all, one replica. It is
deleted at the next reconcile once the switch is off. A connector reaches it only when the live eval
matrix's seeding bound it (`fake_scene: <scene>` in its configuration, which every API write path
refuses); without the switch that field is ignored, and the connector is the server's own instance. So
the server an organisation configured keeps serving its projects: the matrix binds only a server's
connector that nobody configured, turned on for its eval projects alone.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any

import httpx
from choregos_adapters import ConnectorTypeSpec
from choregos_adapters.oci import RegistryError, parse_ref, under_prefix
from choregos_adapters.plugin_servers import (
    TOKEN_ENV,
    PluginServerClient,
    PluginServerRuntime,
    ServerDeployment,
)
from choregos_adapters.registry import FAKE_SCENE_FIELD, fakes_enabled
from choregos_core.egress import host_port_of
from choregos_core.plugins import Plugin, configuration_variables
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.models import ConnectorOperation, OrgConnector
from ..errors import conflict, unprocessable, upstream
from ..logging import get_logger
from ..rbac import Principal
from ..schemas.projects import OrgConnectorServer
from ..secret_store import resolved_configuration, store_ref
from .connector_secrets import drop_replaced_store_values, put_values, require_store

log = get_logger(__name__)

#: The connector's secret field that holds the platform's token for the server. A leading `_` is
#: outside the field pattern of the secret routes: nobody sets or clears it but the platform.
SERVER_TOKEN_FIELD = "_server_token"  # noqa: S105 - a field name, not a secret
KIND, TYPE = "mcp", "mcp"
UNCONFIGURED, DEPLOYED, ERROR = "unconfigured", "deployed", "error"
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_OVERRIDES: dict[str, Any] = {}


# -- the runtime and the transport (tests and fakes) -------------------------------------------------------


def use_plugin_runtime(runtime: PluginServerRuntime | None) -> None:
    """For tests: the runtime the servers are applied through (`None`: back to the default)."""
    _OVERRIDES.pop("runtime", None)
    if runtime is not None:
        _OVERRIDES["runtime"] = runtime


def use_plugin_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: the transport the broker's calls to a server go through (a scripted server)."""
    _OVERRIDES.pop("transport", None)
    if transport is not None:
        _OVERRIDES["transport"] = transport


def transport() -> httpx.AsyncBaseTransport | None:
    """The transport of the platform's calls to a server: a test's, else under `CHOREGOS_FAKES` the
    in-memory server of the tracker profile (`FakePluginTrackerServer`, one per process), else the
    network."""
    if "transport" in _OVERRIDES:
        found: httpx.AsyncBaseTransport = _OVERRIDES["transport"]
        return found
    if not fakes_enabled():
        return None
    fake: httpx.AsyncBaseTransport = fake_tracker_server().transport()
    return fake


def fake_tracker_server() -> Any:
    """The process's `FakePluginTrackerServer` (fakes only): what a fake plugin tracker answers."""
    if "fake_tracker" not in _OVERRIDES:
        from choregos_adapters.fakes.plugin_tracker import FakePluginTrackerServer

        _OVERRIDES["fake_tracker"] = FakePluginTrackerServer()
    return _OVERRIDES["fake_tracker"]


def runtime(settings: Settings | None = None) -> PluginServerRuntime | None:
    """Where servers run: the Kubernetes API of `plugin_servers_namespace`; in memory under
    `CHOREGOS_FAKES`; `None` when the deployment runs none (`plugin_servers: off`)."""
    if "runtime" in _OVERRIDES:
        found: PluginServerRuntime = _OVERRIDES["runtime"]
        return found
    settings = settings or get_settings()
    cilium = settings.plugin_servers_egress == "cilium"
    if fakes_enabled():
        from choregos_adapters.fakes.kubernetes import FakeKubernetesApi

        if "fake" not in _OVERRIDES:
            _OVERRIDES["fake"] = PluginServerRuntime(
                FakeKubernetesApi(), settings.plugin_servers_namespace, cilium=cilium
            )
        fake: PluginServerRuntime = _OVERRIDES["fake"]
        return fake
    if settings.plugin_servers != "kubernetes":
        return None
    from choregos_adapters.executor import KubernetesClient

    return PluginServerRuntime(KubernetesClient(), settings.plugin_servers_namespace, cilium=cilium)


# -- names -------------------------------------------------------------------------------------------------


def object_name(org_id: str, server: str) -> str:
    """One name per organisation and server in the shared namespace, a DNS label."""
    return f"{server[:54]}-{hashlib.sha256(org_id.encode()).hexdigest()[:8]}"


def fake_object_name(org_id: str, server: str) -> str:
    """The name of a server's eval instance (#1372), a DNS label beside `object_name`'s. Its digest is not
    `object_name`'s, so a server named `<other>-fake` of the same organisation never holds the name."""
    return f"{server[:49]}-fake-{hashlib.sha256(f'{org_id}:fake'.encode()).hexdigest()[:8]}"


def service_url(settings: Settings, name: str, port: int, path: str) -> str:
    return f"http://{name}.{settings.plugin_servers_namespace}.svc:{port}{path}"


def _operations(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(op["name"]): dict(op) for op in doc.get("operations") or []}


def _env_names(doc: dict[str, Any]) -> dict[str, str]:
    """field -> the environment variable it reaches the server as: `x-env`, else the upper-cased name."""
    return configuration_variables(doc)


def declaration(plugin: Plugin, doc: dict[str, Any], org_id: str, settings: Settings) -> dict[str, Any]:
    """What the connector keeps of a server: the floor the broker reads, and what runs it."""
    paths = {"mcp": "/mcp", "health": "/healthz", **(doc.get("paths") or {})}
    name = object_name(org_id, str(doc["name"]))
    return {
        "plugin": plugin.name,
        "version": plugin.version,
        "server": doc["name"],
        "image": doc["image"],
        "port": int(doc["port"]),
        "paths": paths,
        "config_schema": doc.get("config_schema") or {"type": "object"},
        "secret_fields": list(doc.get("secret_fields") or []),
        "egress": doc.get("egress") or {},
        "operations": _operations(doc),
        # What the server is besides its operations (S43-07): a tracker, and its webhook.
        "implements": list(doc.get("implements") or []),
        "webhook": doc.get("webhook"),
        "object": name,
        "url": service_url(settings, name, int(doc["port"]), paths["mcp"]),
        # How it runs on its fake for the live eval matrix (#1372), and where that instance answers.
        **_fake_declaration(doc, org_id, settings, paths["mcp"]),
    }


def _fake_declaration(doc: dict[str, Any], org_id: str, settings: Settings, path: str) -> dict[str, Any]:
    fake = doc.get("fake")
    if not isinstance(fake, dict):
        return {}
    name = fake_object_name(org_id, str(doc["name"]))
    return {
        "fake": {"env": dict(fake.get("env") or {}), "scenes": list(fake.get("scenes") or ["default"])},
        "fake_object": name,
        "fake_url": service_url(settings, name, int(doc["port"]), path),
    }


def spec_of_connector(instance: OrgConnector) -> ConnectorTypeSpec | None:
    """A plugin server's connector is configured by the server's own schema and secret fields."""
    server = instance.plugin_server
    if server is None:
        return None
    return ConnectorTypeSpec(
        f"plugin server {server.get('plugin')}/{server.get('server')}",
        (KIND,),
        dict(server.get("config_schema") or {"type": "object"}),
        tuple(server.get("secret_fields") or ()),
    )


def console_view(instance: OrgConnector) -> OrgConnectorServer | None:
    """The server's plugin and the schema its configuration follows, as the connector routes answer it
    (S43-08): the console configures a server with its own form, not the `mcp` type's."""
    server = instance.plugin_server
    if server is None:
        return None
    return OrgConnectorServer(
        plugin=str(server.get("plugin") or ""),
        version=str(server.get("version") or ""),
        server=str(server.get("server") or instance.name),
        config_schema=dict(server.get("config_schema") or {"type": "object"}),
        secret_fields=[str(field) for field in server.get("secret_fields") or ()],
    )


# -- checks before anything is written ---------------------------------------------------------------------


async def _check_image(image: str, settings: Settings) -> None:
    from .hosted_images import image_verifier

    loc = [{"loc": ["servers", "image"], "msg": image}]
    try:
        ref = parse_ref(image)
    except RegistryError as exc:
        raise unprocessable(f"the server image `{image}` cannot be used: {exc}", loc) from exc
    if not ref.digest:
        raise unprocessable(f"the server image `{image}` is not pinned by digest", loc)
    allowed = settings.plugin_servers_registries
    if not under_prefix(ref, allowed):
        known = ", ".join(allowed) or "none"
        raise unprocessable(
            f"the server image `{ref.name}` is not from a registry the operator allows for plugin servers "
            f"(CHOREGOS_PLUGIN_SERVERS_REGISTRIES: {known})",
            loc,
        )
    if settings.hosted_cosign == "off":
        return
    verdict = await image_verifier().verify(image)
    if verdict.verified:
        return
    if settings.hosted_cosign == "enforce":
        raise unprocessable(f"the server image `{image}` carries no trusted signature: {verdict.detail}", loc)
    log.warning("plugin.server.signature_unverified", image=image, detail=verdict.detail)


async def check(
    session: AsyncSession, org_id: str, plugin: Plugin, settings: Settings, *, replacing: str | None
) -> None:
    """Everything a plugin's servers must pass in this organisation, nothing written: the deployment
    runs servers, each image is allowed (and signed if required), each environment variable is a
    name the server may receive, and no connector the plugin does not own holds a server's name.
    `replacing`: the name of the installed plugin an upgrade replaces."""
    if not plugin.servers:
        return
    if runtime(settings) is None:
        raise unprocessable(
            f"`{plugin.name}` brings a server and this deployment runs none "
            "(`global.plugins.servers.enabled`, CHOREGOS_PLUGIN_SERVERS)"
        )
    require_store({SERVER_TOKEN_FIELD: "-"})
    for name, doc in sorted(plugin.servers.items()):
        await _check_image(str(doc["image"]), settings)
        for field, env in _env_names(doc).items():
            if not _ENV.match(env) or env.startswith("CHOREGOS_"):
                raise unprocessable(
                    f"server `{name}`: `{field}` reaches the server as `{env}`, which is not a variable it "
                    "may receive (upper case, not `CHOREGOS_*`)"
                )
        found = await _connector(session, org_id, name)
        owners = {plugin.name, *([replacing] if replacing else [])}
        if found is not None and (found.plugin_server or {}).get("plugin") not in owners:
            raise conflict(
                f"the organisation already has a connector `{name}`: the server `{name}` of `{plugin.name}` "
                "would take its name (an agent's tools are `<server>__<operation>`). "
                "Rename or delete it first"
            )


# -- install, upgrade, uninstall ---------------------------------------------------------------------------


async def _connector(session: AsyncSession, org_id: str, name: str) -> OrgConnector | None:
    found = await session.execute(
        select(OrgConnector).where(OrgConnector.org_id == org_id, OrgConnector.name == name)
    )
    return found.scalar_one_or_none()


async def connectors_of(session: AsyncSession, org_id: str, plugin: str) -> list[OrgConnector]:
    rows = await session.execute(
        select(OrgConnector).where(OrgConnector.org_id == org_id, OrgConnector.plugin_server.is_not(None))
    )
    return [c for c in rows.scalars() if (c.plugin_server or {}).get("plugin") == plugin]


def floor_policy(op: dict[str, Any]) -> str:
    """A read is allowed, a write needs an approval (ADR 0034); `human_approval` never goes lower."""
    return "allowed" if op.get("access") == "read" and not op.get("human_approval") else "approval"


async def _sync_operations(session: AsyncSession, instance: OrgConnector) -> None:
    """One row per declared operation: a new one at its floor; a kept one keeps the organisation's
    policy, groups and price, its declared access and schema follow the server; a row the server no
    longer declares goes."""
    declared = (instance.plugin_server or {}).get("operations") or {}
    rows = {
        op.name: op
        for op in (
            await session.execute(
                select(ConnectorOperation).where(ConnectorOperation.connector_id == instance.id)
            )
        ).scalars()
    }
    for name, op in sorted(declared.items()):
        row = rows.pop(name, None)
        if row is None:
            session.add(
                ConnectorOperation(
                    org_id=instance.org_id,
                    connector_id=instance.id,
                    name=name,
                    access=str(op["access"]),
                    policy=floor_policy(op),
                    groups=[],
                    input_schema=op.get("input_schema"),
                    description=op.get("description") or None,
                )
            )
            continue
        if row.access != op["access"]:
            row.access, row.policy = str(op["access"]), floor_policy(op)
        row.description = op.get("description") or row.description
        if op.get("input_schema") is not None:
            row.input_schema = op["input_schema"]
    for row in rows.values():
        await session.delete(row)
    await session.flush()


async def provision(
    session: AsyncSession, org_id: str, plugin: Plugin, principal: Principal, settings: Settings
) -> list[str]:
    """Each server of `plugin` as a connector of the organisation, running: created, or brought to the
    plugin's new declaration on an upgrade; a server the plugin no longer brings is removed. Replayed,
    the same rows and objects. Returns the servers' names."""
    for gone in await connectors_of(session, org_id, plugin.name):
        if gone.name not in plugin.servers:
            await _remove(session, gone, principal, settings)
    for name, doc in sorted(plugin.servers.items()):
        decl = declaration(plugin, doc, org_id, settings)
        instance = await _connector(session, org_id, name)
        if instance is None:
            store = require_store({SERVER_TOKEN_FIELD: "-"})
            prefix = f"connector/{name}"
            instance = OrgConnector(
                org_id=org_id,
                name=name,
                kind=KIND,
                type=TYPE,
                config={},
                secret_refs={SERVER_TOKEN_FIELD: store_ref(f"{prefix}/{SERVER_TOKEN_FIELD}")},
                created_by=f"plugin:{plugin.name}",
                catalogue_entry=f"plugin:{plugin.name}/{name}@{plugin.version}"[:128],
                status=UNCONFIGURED,
                plugin_server=decl,
            )
            session.add(instance)
            await session.flush()
            await put_values(
                session, store, org_id, prefix, {SERVER_TOKEN_FIELD: secrets.token_urlsafe(32)}, None
            )
            await record(
                session, principal, "org.connector.create", org_id=org_id, target_type="org_connector",
                target_id=instance.id, name=name, type=TYPE, plugin=plugin.name,
            )  # fmt: skip
        else:
            instance.plugin_server = decl
            instance.catalogue_entry = f"plugin:{plugin.name}/{name}@{plugin.version}"[:128]
        await _sync_operations(session, instance)
        await reconcile(session, instance, settings)
    return sorted(plugin.servers)


async def _remove(
    session: AsyncSession, instance: OrgConnector, principal: Principal, settings: Settings
) -> None:
    found = runtime(settings)
    server = instance.plugin_server or {}
    if found is not None and server.get("object"):
        await _apply(found.delete(str(server["object"])), instance)
    await drop_replaced_store_values(session, require_store(None), instance.org_id, instance.secret_refs, {})
    await session.delete(instance)
    await session.flush()
    await record(
        session, principal, "org.connector.delete", org_id=instance.org_id, target_type="org_connector",
        target_id=instance.id, name=instance.name, plugin=server.get("plugin"),
    )  # fmt: skip


async def deprovision(
    session: AsyncSession, org_id: str, plugin: str, principal: Principal, settings: Settings
) -> list[str]:
    """Uninstall: each server's objects deleted, its connector with its operations and sealed values."""
    removed = []
    for instance in await connectors_of(session, org_id, plugin):
        removed.append(instance.name)
        await _remove(session, instance, principal, settings)
    return sorted(removed)


# -- the objects -------------------------------------------------------------------------------------------


def _env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return ",".join(str(v) for v in value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def egress_of(server: dict[str, Any], config: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    """The (host, port) the server may reach: the hosts of its `from_fields`, or its fixed `hosts`."""
    egress = server.get("egress") or {}
    found: set[tuple[str, int]] = {(str(h).lower(), 443) for h in egress.get("hosts") or []}
    for field in egress.get("from_fields") or []:
        hp = host_port_of(config.get(field))
        if hp is not None:
            found.add(hp)
    return tuple(sorted(found))


def configuration_problems(server: dict[str, Any], config: dict[str, Any], *, partial: bool) -> list[str]:
    """What the configuration breaks of the server's schema; `partial`: required fields not asked."""
    import jsonschema

    schema = dict(server.get("config_schema") or {"type": "object"})
    if partial:
        schema.pop("required", None)
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.validator}"
        for e in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path))
    ]


async def check_egress(session: AsyncSession, instance: OrgConnector, config: dict[str, Any]) -> None:
    """At configuration: every host the server would reach passes the organisation's network bounds
    (ADR 0070): a deny wins, an allowed set bounds it."""
    from .platform_network import check_platform_host

    server = instance.plugin_server or {}
    for host, port in egress_of(server, config):
        await check_platform_host(
            session, instance.org_id, f"https://{host}:{port}", f"server `{instance.name}`"
        )


async def _apply(awaitable: Any, instance: OrgConnector) -> None:
    from choregos_adapters.errors import UpstreamError

    try:
        await awaitable
    except (UpstreamError, httpx.HTTPError) as exc:
        raise upstream("kubernetes", f"the server `{instance.name}` cannot be applied: {exc}") from exc


async def reconcile(session: AsyncSession, instance: OrgConnector, settings: Settings | None = None) -> str:
    """The server's objects as its declaration and configuration say: idempotent. Returns the status
    (`deployed`, or `unconfigured` while the configuration does not satisfy the schema: no replica)."""
    settings = settings or get_settings()
    server = instance.plugin_server
    found = runtime(settings)
    if server is None or found is None:
        return instance.status
    from choregos_core.secrets import SecretNotFound

    try:
        resolved = await resolved_configuration(
            session, instance.org_id, KIND, TYPE, instance.config, instance.secret_refs
        )
    except SecretNotFound as missing:
        raise unprocessable(
            f"the server `{instance.name}` cannot read its configuration: {missing}"
        ) from missing
    token = str(resolved.pop(SERVER_TOKEN_FIELD))
    names = _env_names(server)
    env = {names[f]: _env_value(v) for f, v in resolved.items() if f in names and v is not None}
    env[TOKEN_ENV] = token
    problems = configuration_problems(
        server, {k: v for k, v in resolved.items() if k in names}, partial=False
    )
    d = ServerDeployment(
        name=str(server["object"]),
        namespace=settings.plugin_servers_namespace,
        image=str(server["image"]),
        port=int(server["port"]),
        health_path=str(server["paths"]["health"]),
        env=env,
        labels={
            "app.kubernetes.io/name": "choregos-plugin-server",
            "app.kubernetes.io/managed-by": "choregos-api",
            "choregos.dev/plugin": str(server["plugin"]),
            "choregos.dev/server": str(server["server"]),
            "choregos.dev/org": instance.org_id,
        },
        replicas=0 if problems else 1,
        runtime_class=settings.plugin_servers_runtime_class or None,
        cpu=settings.plugin_servers_cpu,
        memory=settings.plugin_servers_memory,
        api_namespace=settings.plugin_servers_api_namespace,
        egress=egress_of(server, resolved),
        egress_mode=settings.plugin_servers_egress,
    )
    await _apply(found.apply(d), instance)
    await _reconcile_fake(found, instance, server, token, settings)
    instance.status = UNCONFIGURED if problems else DEPLOYED
    instance.last_error = ("configuration incomplete: " + "; ".join(problems))[:1000] if problems else None
    await session.flush()
    return instance.status


def fake_deployment(server: dict[str, Any], org_id: str, token: str, settings: Settings) -> ServerDeployment:
    """A server's eval instance (#1372): its image with its fake's variables and the platform's token,
    nothing of the configuration, no egress, one replica."""
    fake = server.get("fake") or {}
    env = {str(k): str(v) for k, v in (fake.get("env") or {}).items()}
    env[TOKEN_ENV] = token
    return ServerDeployment(
        name=str(server["fake_object"]),
        namespace=settings.plugin_servers_namespace,
        image=str(server["image"]),
        port=int(server["port"]),
        health_path=str(server["paths"]["health"]),
        env=env,
        labels={
            "app.kubernetes.io/name": "choregos-plugin-server",
            "app.kubernetes.io/managed-by": "choregos-api",
            "choregos.dev/plugin": str(server["plugin"]),
            "choregos.dev/server": str(server["server"]),
            "choregos.dev/org": org_id,
            "choregos.dev/fake": "true",
        },
        replicas=1,
        runtime_class=settings.plugin_servers_runtime_class or None,
        cpu=settings.plugin_servers_cpu,
        memory=settings.plugin_servers_memory,
        api_namespace=settings.plugin_servers_api_namespace,
        egress=(),
        egress_mode=settings.plugin_servers_egress,
    )


async def _reconcile_fake(
    found: PluginServerRuntime, instance: OrgConnector, server: dict[str, Any], token: str, settings: Settings
) -> None:
    """The eval instance of a server that declares its fake: running where the deployment serves eval
    fakes, deleted everywhere else (an absent one is already deleted)."""
    from choregos_adapters.fakes.scenes import eval_fake_scenes_allowed

    if not server.get("fake_object"):
        return
    if eval_fake_scenes_allowed():
        await _apply(found.apply(fake_deployment(server, instance.org_id, token, settings)), instance)
    else:
        await _apply(found.delete(str(server["fake_object"])), instance)


# -- the broker's client -----------------------------------------------------------------------------------


def bound_fake(instance: OrgConnector) -> str | None:
    """The scene a plugin server's connector is bound to on its eval instance (#1372): `fake_scene` in
    its configuration, written by the live eval matrix's seeding only, honoured where the deployment
    serves eval fakes and the server declares that scene; `None` otherwise — the connector is then the
    server's own instance."""
    from choregos_adapters.fakes.plugin_servers import bound_plugin_fake

    server = instance.plugin_server
    if server is None or not server.get("fake_url"):
        return None
    return bound_plugin_fake(server, instance.config)


def refuse_fake_binding(config: dict[str, Any]) -> None:
    """No declaration or edit binds a plugin server's connector to its eval instance (#1372): only the
    live eval matrix's seeding writes `fake_scene`, as for a connector on a scene (S57-11a)."""
    if FAKE_SCENE_FIELD in config:
        raise unprocessable(
            f"`{FAKE_SCENE_FIELD}` binds a server's connector to its eval instance for the live eval matrix "
            "(#1372): only the matrix's seeding writes it, never a declaration or an edit",
            [{"loc": ["body", "config", FAKE_SCENE_FIELD], "msg": "refused"}],
        )


async def server_endpoint(session: AsyncSession, instance: OrgConnector) -> tuple[str, str] | None:
    """`(url, bearer token)` of a plugin's server — of its eval instance for a connector the live eval
    matrix bound to it (`bound_fake`); `None` for any other connector."""
    server = instance.plugin_server
    if server is None:
        return None
    url = str(server["fake_url"]) if bound_fake(instance) is not None else str(server["url"])
    resolved = await resolved_configuration(
        session,
        instance.org_id,
        KIND,
        TYPE,
        {},
        {SERVER_TOKEN_FIELD: (instance.secret_refs or {})[SERVER_TOKEN_FIELD]},
    )
    return url, str(resolved[SERVER_TOKEN_FIELD])


async def plugin_client(
    session: AsyncSession, instance: OrgConnector, *, bearer: str | None = None
) -> PluginServerClient | None:
    """The client of a plugin's server, with its bearer token; `None` for any other connector.

    `bearer`: the person's exchanged token for one call (ADR 0135), which replaces the server's own."""
    if bearer is not None and instance.plugin_server is not None:
        url = str(
            instance.plugin_server["fake_url"] if bound_fake(instance) else instance.plugin_server["url"]
        )
        return PluginServerClient(url, token=bearer, transport=transport())
    endpoint = await server_endpoint(session, instance)
    if endpoint is None:
        return None
    return PluginServerClient(endpoint[0], token=endpoint[1], transport=transport())


async def connector_client(
    session: AsyncSession, instance: OrgConnector, *, bearer: str | None = None
) -> Any:
    """The client the platform calls an organisation connector with: a plugin's server through its
    Service and bearer token; any other through its type's adapter, its secrets resolved here.

    A connector the live eval matrix declared on a fake's scene (`fake_scene`, S57-11a) is served by
    that scene, built anew, when the deployment serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES`); the API
    refuses the field on every declaration and edit, so no other connector carries it.

    `bearer` (ADR 0135): the person's exchanged token, for this call only. It is the connector's bearer in
    place of its own token, which is not even resolved."""
    client = await plugin_client(session, instance, bearer=bearer)
    if client is not None:
        return client
    from choregos_adapters import build
    from choregos_adapters.fakes.scenes import bound_scene

    if (bound := bound_scene(instance.kind, instance.config)) is not None:
        return bound.connector

    from .platform_network import with_identity

    refs = instance.secret_refs
    if bearer is not None:
        refs = {name: ref for name, ref in (refs or {}).items() if name != "token"}
    config = await resolved_configuration(
        session, instance.org_id, instance.kind, instance.type, instance.config, refs
    )
    if bearer is not None:
        config = {**config, "token": bearer, "auth_scheme": "bearer", "auth_header": "Authorization"}
    return build(instance.kind, instance.type, await with_identity(session, instance.org_id, config))


__all__ = [
    "DEPLOYED",
    "SERVER_TOKEN_FIELD",
    "UNCONFIGURED",
    "bound_fake",
    "check",
    "check_egress",
    "configuration_problems",
    "connector_client",
    "connectors_of",
    "declaration",
    "deprovision",
    "egress_of",
    "fake_deployment",
    "fake_object_name",
    "fake_tracker_server",
    "floor_policy",
    "object_name",
    "plugin_client",
    "provision",
    "reconcile",
    "refuse_fake_binding",
    "runtime",
    "server_endpoint",
    "spec_of_connector",
    "transport",
    "use_plugin_runtime",
    "use_plugin_transport",
]
