# Plan — 2026-11-11: outposts, tools that live where the platform cannot reach (stream S65)

Source: the owner's design of 2026-11-11, approved the same day. It is French and stays outside the
repository (ADR 0046). It calls the concept by a working name; the owner then chose **outpost**, because
the working name already means the installation itself in this code (`config.py`, `procedures_extra` and
its `<id>.site.md` notes; `schemas/catalogues.py`, `origin: "catalogue" | "site"`; `docs/deployment.md`,
"Site notes on the setup procedures"). Everywhere: the concept `outpost`, the daemon and package
`choregos-outpost` / `packages/outpost`, the door `/outposts/v1`, the connector type `mcp-outpost`, the
chart `charts/choregos-outpost`, the setting `CHOREGOS_OUTPOSTS`, the permission `outpost:manage`, the
principal kind `outpost`.

Checked against `main` at `9f646fda4`. Stream number: **S65**. ADRs: **0147-0149** (proposed):
[0147](../adr/0147-an-outpost-only-dials-out.md) the concept,
[0148](../adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) the door,
[0149](../adr/0149-a-tool-may-live-at-an-outpost.md) the connector. S65-01 (ADR 0143, a run that ended
stays ended) and S65-02 (ADR 0144, the run's API answers only where runs are) are hardening stories of the
same stream, each with its own pull request and its own STATUS row; they are not described here. Story ids
S65-10 to S65-19 are the design's.

Status words: **exists** (in the code), **missing**, **partial** (exists, and does not cover the need).

## 0. What the audit found

| What an outpost needs | Where | Today |
|:--|:--|:--|
| One place where a connector's route is decided | `services/mcp_gateway.py`: `Route` (454), `route_of` (461), `connector_client` (493); called by `services/broker.py` (270, 401, 569, 590), `effects.py` (124, 189), `routers/org_connectors.py` (618, 626) | **exists**: a host and "is it the gateway's"; no third route |
| A connector with no URL and no secret | `packages/adapters/src/choregos_adapters/specs.py` (763: `mcp-gateway`, `server` and `timeout_s`, no secret field, `egress` empty), `builtins.py` (181, 725) | **exists** as the precedent; `mcp-outpost` **missing** |
| A client the callers can swap | they call `call_tool` / `list_tools` when the client has them (`broker.py` 598, `effects.py` 194, `org_connectors.py` 629) | **exists**; a new subclass of the MCP client is refused (`tests/licences/test_mcp_gateway_boundary.py`, `RUNTIME_MCP_CLIENTS`, 246) |
| Governance around the call, not at the server | `broker.py`: `tools_of_selection` (192), `propose_call` (258), `write_through_action` (386), `check_arguments` (511), `bounded_result` (664); `routers/internal.py`: `callRunTool` (969), `_injection_guard` (1384), the ledger line and the `tool.called` digests | **exists**, all of it in the API |
| A deterministic id for a write | `broker.py` `call_id` (380); `apps/orchestrator/src/choregos_orchestrator/activities/actions.py` `execute_effect` (93): the key `<action id>:<position>`, recorded `started` before each attempt | **exists** |
| A write that is not retried, and a state that says "unknown" | `apps/orchestrator/src/choregos_orchestrator/workflows/action.py`: `EFFECT` retries six attempts (41), any final failure compensates and closes `failed` (97-120); `ActionStatus` (`packages/contracts/src/choregos_contracts/enums.py`, 234) has no such state | **missing** |
| An idempotency key the platform fills | `effects.py` `_platform_arguments` (220): `action_key`; declared by `OperationSpec.platform_arguments` (`registry.py`, 59) or a plugin server's descriptor (`services/connectors.py`, `declared_operation`, 25) | **exists** for declared operations; **missing** for a discovered tool (`declared_operation` answers `None`) |
| A connector type that says which identities it supports | `ConnectorTypeSpec` (`registry.py`, 91) | **missing**: no such field; ADR 0107's story S50-01 is not started |
| Sealing to one recipient | `packages/adapters/src/choregos_adapters/sealing.py`: `seal`, `open_sealed` (X25519, HKDF-SHA256, ChaCha20-Poly1305, the run's id as associated data) | **exists**, one direction, platform to pod |
| A request signed by an Ed25519 key | run tokens are ES256 (`security.py`, 22); the installed PyJWT lists `EdDSA` | **missing**; no new dependency needed |
| A wake-up that reaches every replica | `events.py` `PostgresRelay` (162): one `LISTEN` connection per replica, channel `choregos_events` | **partial**: `_receive` (179) drops what is not a platform event; none on SQLite (`main.py`, 105) |
| A long poll that holds no transaction | `routers/agent_session_pods.py` `get_next_turn` (106): a look every 0.2 s (`POLL_S`), 30 s at most | **partial**: polled, not woken; under the all-organisations scope (64) |
| Row-level security that names one organisation, then one outpost | `db/session.py`: `_CARRIED` (68), `limit_to_organisations` (186); the policy shape in `migrations/versions/20261027_1200_org_plugins.py` (91-103); the guard `apps/api/tests/test_rls_postgres.py` (398) | **exists** for the organisation, fail closed; a second setting **missing** |
| A principal that is not a person | `rbac.py` `Principal.kind` (108); `deps.py` (114) `machine`; `rbac.py` (153) `system` | **exists**; `outpost` **missing** |
| A permission only the administrator holds | `rbac.py`: `Permission` (13), `Role.ORG_ADMIN: frozenset(Permission)` (103) | **exists** as a mechanism |
| Doors frozen by a test | `tests/licences/test_mcp_gateway_boundary.py`: `DOOR_DIRS` (82), `FORBIDDEN_MODULES` (86), `DOOR_ROUTES` (141); `main.py` (407-415) | **exists**, for two doors |
| A contract for a door | `packages/contracts/openapi.yaml` and `schemas/`; `/mcp` and `/a2a` are outside the contract (`tests/web/test_api_routes_have_a_client.py`, 36) | **missing** |
| A replay cache | `rate_limit.py` (5-6): "There is no shared store, by decision" | **missing**, on purpose: ADR 0148 uses a sequence on a row |
| Encryption at rest behind a seam | `secret_store.py` `KeyWrapper` (124) | **exists** |
| An entrypoint of the API image as its own Deployment | `charts/choregos/templates/egress.yaml` (19-53); the relay `apps/api/src/choregos_api/relay/`, which no chart template renders (`docs/usage.md`, 110) | **partial**: neither holds a database credential |
| A bounded connection pool per process | `config.py` (61-68): `db_pool_size`, `db_max_overflow` | **exists** |
| A network journal | `db/models/audit.py` `NetworkDecision` (43): a row per run, layer, host and decision; `services/platform_network.py` `enforce_connector_call` (190) checks nothing without a host (205) | **exists** |
| "What a version reaches", read before a run | `services/agent_reach.py`: a connector tool is `{name, source, origin, access}` | **partial**: no route, no offline state |
| A port in `packages/adapters` the API implements | `adapter_wiring.py` (22-54); `tests/architecture/test_packages_imports_of_the_api.py` | **exists** as the precedent |
| Enrolment, a lease, a heartbeat, a peer that only dials out, a daemon, a second chart | searched by name over the tree | **missing**: nothing of the kind |

## 1. Departures from the design (the code wins)

- **Numbers.** The design numbered its records from 0143. 0143-0146 and 0150 belong to sibling stories of
  the stream, so these three are 0147-0149.
- **Identity modes are not declared by a connector type yet.** The design has `mcp-outpost` declare the
  one mode it supports. `ConnectorTypeSpec` has no such field, and ADR 0107 names the modes *assigned*,
  *own* and *on behalf of a person*. ADR 0149 §2 states the rule in those words (the `service` identity is
  ADR 0107's *assigned*); S65-13 enforces it with what exists when it lands.
- **A discovered tool cannot declare an idempotency key.** The design lets "an operation that declares an
  idempotency key" be delivered again. Only a connector type or a plugin server's descriptor declares
  `platform_arguments`; an `mcp-outpost` connector's tools are discovered. The statement therefore lives
  in the organisation's operation rule, and "refused at declaration" becomes "refused when the write is
  opened" (ADR 0149 §6): a discovered write is born `forbidden`, and opening it is the moment a person
  decides.
- **`in_doubt` is not activity code alone.** The design places it in `effects.py` and
  `activities/actions.py`. The retry of an effect and the compensation after a failure are in
  `workflows/action.py`: a claimed write must go through neither, which is a patched branch of the
  workflow and a replayed history (AGENTS.md, "No PR on `workflows/` without a replayed history").
- **The call id of a write is the effect's key, not `call_id`.** `call_id` is the id of the action a
  run's call creates; a write may also come from a workflow transition, and a read has no id at all. The
  key `execute_effect` records before each attempt exists on every path a write takes.
- **`BUILT_UNDER_FAKES` is not how the precedent works.** The design adds the type to that set
  (`registry.py`, 346). `mcp-gateway` is not in it: under fakes the `mcp` fake factory branches on what
  the platform injected (`builtins.py`, 146-153), and the type "cannot be built from its configuration
  alone" (`specs.py`). An outpost client needs the rendezvous the API owns, so S65-13 follows the gateway.
- **The relay does not carry arbitrary wake-ups.** "Woken by the existing relay" needs an event type in
  the event contract or a second channel on the same connection (ADR 0148, consequences).
- **The relay of ADR 0091 is not a chart precedent.** It is an entrypoint of the API image, deployed by
  an operator; the chart renders the egress proxy that way. A Deployment that serves one router with a
  database pool of its own is new (ADR 0148 §8).
- **The documentation story cannot go first.** The design gives S65-18 no dependency.
  `tests/docs/test_docs_name_existing_paths.py` refuses a page that names a path the tree does not hold, so
  S65-18 lands after the stories whose files it names.

Left open by the design and decided in the records, each to be confirmed at their review: the outpost
reads "is it a write" from its own server, never from the call (ADR 0147 §6); the outpost setting of
row-level security fails closed like the organisation's (ADR 0148 §6); the key an answer is sealed to
never travels in a field a proxy could rewrite, and the contract fixes the envelope (ADR 0148 §5); an
in-doubt action is not compensated automatically (ADR 0149 §5); a call that reached no server is not
booked (ADR 0149 §7).

## 2. Stories

Order: S65-10 freezes the contract. From there two lines run in parallel: the platform's (S65-11, then
S65-12, S65-13, S65-14) and the daemon's (S65-15, against the contract and a fake door of its own).
S65-17 starts once S65-11 has merged its management routes. S65-16 needs the door and the daemon, S65-18
the code it names, S65-19 everything. Nothing is reachable before `CHOREGOS_OUTPOSTS` is turned on: every
story merges switched off.

### S65-00 · docs(adr): ADRs 0147-0149, this plan, the backlog stories

### S65-10 · contract(outposts): the door's contract v1 and the management API

`packages/contracts/outpost-door.yaml` (the four verbs, the signature's claims, the sealed envelope, the
version window, the closed list of refusal codes), `schemas/outpost-policy.schema.json` with examples, the
management paths `/orgs/{org}/outposts…` and the connector's shape in `openapi.yaml`, `"/outposts/v1/*"` in
`MACHINE_ONLY_ROUTES`. **Accepted when** `tests/docs/test_outpost_door_contract.py` (on the model of
`test_relay_route_contract.py`) validates the examples, refuses a fifth verb and a path parameter named
`connector` or `server`; one invalid policy example per rule is refused with the field's path; `make
contracts` is regenerated and `make contracts-check` is green.

### S65-11 · feat(api): outposts, instances, enrolments: tables, row-level security, rights, management routes

Tables `outposts`, `outpost_instances`, `outpost_enrolments`, `outpost_calls`, and the connector's
reference to its outpost; `Permission.OUTPOST_MANAGE`; the principal kind; audit events `outpost.create`,
`outpost.enrol`, `outpost.approve`, `outpost.revoke`, `outpost.pause`. Depends on S65-10. **Accepted when**
`apps/api/tests/test_outposts_api.py` shows: an enrolment token used twice, or after 15 minutes, is
refused; an instance is born pending; an approval with another fingerprint, or by a token instead of a
person's session, is refused; a revocation moves the epoch; without `outpost:manage` the routes answer
403, and with `CHOREGOS_OUTPOSTS` off a creation is refused; an outpost and its enrolment token come back
with the door's `door_keys` to pin (ADR 0148 §13), and the token with the door's public origin, a setting
of the installation; a paused outpost is resumed (`resumeOutpost`); a revoked outpost is deleted
(`deleteOutpost`), and one that still has connectors is not (409). On PostgreSQL:
`test_every_table_is_under_rls_or_exempted` passes with no new exemption; a session that names outpost A
reads and claims no row of outpost B, of the same organisation or another, **from a query with no `WHERE`
clause**; the migration goes down and up again.

### S65-12 · feat(api): the outposts' door: enrol, poll, answer, refuse

`apps/api/src/choregos_api/outposts/`, mounted only when the setting is on. Depends on S65-11. **Accepted
when** `test_outpost_door.py` refuses, each with its own case: a replayed sequence, a valid signature over
another method or URL, a stale `iat`, a stale nonce, a revoked instance, a pending instance, an answer to
another outpost's call (404, never 403), a body above the cap (before parsing), a second concurrent poll
(conflict and an alert), a daemon below the minimum version (a refusal that names the minimum).
`test_outpost_rendezvous_postgres.py`: of two claimers one wins; a claimed write is never claimed again; an
answer under a previous epoch is refused; no call heavier than the contract's bound
(`max_sealed_call_bytes`) is handed over; a cancel does not interrupt a write: the answer that still comes
meets `lease_lost` (S65-10b). `test_outpost_long_poll_holds_no_connection.py`: a hundred waiting polls hold
no pooled connection; a call written is handed over within a second, and still within the periodic look
when the relay is stopped. `test_outpost_door_conformance.py`: the live routes are the contract's, every
call the door hands over is signed with its key (ADR 0148 §13), and the contract's vectors verify. The
boundary test is green with the directory in `DOOR_DIRS` and the four routes in `DOOR_ROUTES`, and red when
the door imports the broker.

### S65-13 · feat(api,adapters): a connector reached through an outpost, and its fakes

The port and the tool client in `packages/adapters/src/choregos_adapters/outpost/`, implemented over the
database by the API as `adapter_wiring.py` does for the lexical memory; a scriptable `FakeOutpost`
(`go_offline`, `refuse_next`, `lose_lease`); the type `mcp-outpost`; the outpost route in `route_of` and
`connector_client`; the typed refusals; the procedure `connector.mcp.mcp-outpost.md`. Depends on S65-12.
**Accepted when** `apps/api/tests/test_broker_via_outpost.py` shows that the same call through a direct
connector and through an outpost gives the same bounded result, the same ledger line and the same
`tool.called` digests; a forbidden operation, an argument outside its grant and an operation under approval
write no rendezvous row; a call whose envelope would exceed the contract's bound is refused where it is
admitted and writes none either; `result_sha256` is written when the answer is opened, by the side that
placed the call, never by the door (S65-10b); an offline outpost is a refusal that names it, within the
connector's timeout, and is not booked; a local refusal is a 422 with its code. Discovery through an
outpost adds tools born `forbidden`. The gateway's mode `required` does not refuse an `mcp-outpost`
connector; a declaration with a URL or a secret is a 422. No class extends the MCP client; `make
procedures-check` is green.

### S65-14 · feat(api,orchestrator): a write is delivered at most once, and may end `in_doubt`

`ActionStatus.in_doubt` (contract change), the operation rule's two statements, the effect and its
workflow. Depends on S65-13. **Accepted when**, with the fake outpost: a write whose lease is lost ends
`in_doubt` on the effect and on the action, the server received exactly one call, nothing was compensated
and no route replays it; an effect retried after its claim writes no second rendezvous row; an agent that
repeats the call gets the same action, in doubt; a write nobody claimed is retried and succeeds once the
outpost is back; an operation whose rule lists `action_key` is delivered again with the same key; opening
a write with neither statement is a 422 that names both. `tests/replay` is green with the histories
recorded before, and a new history covers the in-doubt branch.

### S65-15 · feat(outpost): the daemon `choregos-outpost` (`enrol | run | doctor`)

`packages/outpost/` (`choregos_outpost`: identity, uplink, policy, audit, tool host, journal, doctor,
fakes), `docker/outpost.Dockerfile`, the release's signed image and SBOM. It calls its endpoints with the
MCP client of `choregos_adapters`. Depends on S65-10 only. **Accepted when** its tests show: a signed
request the contract's fixtures accept, a backoff with jitter, and a daemon that stops polling and says so
when the door's minimum version is above its own; a tool outside its patterns, a tool its server does not
declare read-only under `writes: deny`, an unknown alias and a payload above its ceiling are each refused
with a code and a local audit line, the server having received nothing; a call that is unsigned, or signed
by a key its policy file does not pin, is refused `signature_refused` and logged without its envelope being
opened (ADR 0148 §13); a daemon killed in the middle of a write and restarted reports the call unanswered
and does not execute it again; a call id seen twice is answered from the journal, and a copy of a call
still under way leaves a `replayed` audit line with no outcome; a call it cannot journal or log is refused
`cannot_record`, its server having received nothing (S65-10b); `doctor` fails, one test per check, on DNS,
the TLS chain behind a private certificate authority, the outbound proxy, clock drift, the door, each
alias, the policy's validity and `door_keys` that do not parse as keys. No private key appears in an output
or a log line. `tests/packaging/test_distributables.py` lists the package, and the ceiling of
`tests/architecture/test_packages_imports_of_the_api.py` does not move.

### S65-16 · feat(charts): the door's own Deployment and route; the chart `choregos-outpost`

`global.outposts.enabled` (false), the door's hostnames, an optional dedicated Deployment with its pool,
the `/outposts` route in both entry forms; `charts/choregos-outpost/`: the daemon, a ServiceAccount bound
to no role, the identity Secret, the policy ConfigMap, a NetworkPolicy, and an optional `servers:` list of
servers the operator chooses to run beside it. Depends on S65-12 and S65-15. **Accepted when**
`tests/charts/test_outpost_door_routed.py` (on the model of `test_a2a_door_routed.py`) shows: disabled,
nothing of the door is rendered; enabled, the route serves `/outposts` under the door's hostnames only; a
door hostname that is also a console hostname fails the render; dedicated, a Deployment serves the door
alone; enabled, the chart generates the door's signing key once, keeps it across upgrades and mounts it
where the door runs (ADR 0148 §13). `tests/charts/test_outpost_chart.py`: the pod is locked down as a
plugin server's is (ADR 0096 §2), and its NetworkPolicy lets egress through to the control plane and the
listed Services only. `make charts-lint` is green for both charts.

### S65-17 · feat(cli,web): `choregos outposts`, the Outposts page, the connector form, the access tab

`choregos outposts create | list | show | approve | revoke | pause | resume | delete`; an administration
page (presence, pending instances with the fingerprint to approve, pause, the last calls and their refusal
codes); the form of a connector at an outpost; the outpost and its offline state on an agent's access tab.
Depends on S65-11 (and on S65-13 for the form and the tab). **Accepted when** every management route has a
client (`tests/web/test_api_routes_have_a_client.py`, no new entry in `NO_CLIENT_YET`); `docs/cli.md` is
regenerated; the web tests show that a pending instance cannot be approved without its fingerprint
confirmed, that a paused outpost says so, and that the access tab names the outpost and `offline`; the
console and the CLI print, beside the enrolment command, the `door_keys` lines to put in the policy file
(ADR 0148 §13), and resuming and deleting an outpost have their client.

### S65-18 · docs: outposts for whoever operates the platform, and for whoever runs one

`docs/outposts.md` and `docs/outpost-operator.md`; lines in `docs/security.md`, `docs/deployment.md`,
`docs/concepts.md` and `docs/positioning.md`. Depends on S65-13, S65-15, S65-16 and S65-17. **Accepted
when** `tests/docs` is green; the operator's guide carries, each under a heading a test looks for: what
the daemon does and cannot do, the one firewall rule, the policy file, the door's keys it pins and how
they are rotated (ADR 0148 §13), the local audit log, upgrades that are pulled and verified, who is
responsible for what, and that an outpost does not give data residency;
the commands it shows are the ones the daemon's help prints; the platform's page offers an organisation's
own MCP gateway (ADR 0105) before it describes an outpost.

### S65-19 · test(e2e): a tool call through an outpost, end to end

`tests/e2e/test_outpost_tools.py` on fakes, `tests/cluster/test_outpost_tools_on_cluster.py` on a real
cluster. Depends on every story above. **Accepted when**, on fakes: an outpost is created, enrolled,
approved and run; a connector is declared and discovered, its tools born forbidden; an opened read called
by a run gives one bounded result and one ledger line; with the daemon stopped the call fails fast and
names the outpost; with the alias removed from the policy the call is refused and the local log says so;
a write whose lease is lost is `in_doubt` and the server received one call. On a cluster whose network
plugin enforces policies (`make test-cluster`): the same read succeeds while a connection from the
platform's namespace to the outpost's times out, which a bench that does not enforce policies cannot
prove.

## 3. Later lots, not opened here

- **A client or an assistant drafts a person's task; the person decides.** After the assistants stream
  has landed: the draft reuses that stream's proposals and their confirmation, it does not add a second
  mechanism. The step's actor stays a person, and no tool of a door decides (ADR 0030 §5).
- **Runs at a managed Kubernetes outpost**, on a named need only, in a stream of its own, once the
  conditions of ADR 0149 §12 hold.
- **A personal machine that executes its owner's personal space, and nothing else.** Optional. Never the
  work of a shared project (ADR 0147, what is refused).

Between this lot and those, a decision point: outposts in service are measured (how many, calls, added
latency, local refusals, in-doubt actions) before anything more is built.

## 4. Outside this repository

- **The operator's network**: the door's hostname, its DNS record and its certificate; the rate limit and
  the source restrictions at the entrance; the deployment's values.
- **The outpost operator's side**: their policy file, the one firewall rule (one hostname, port 443,
  outbound), their outbound proxy and its certificate authority, the servers they deploy and the
  credentials those hold, how long they keep the local audit log.
- **A shared-responsibility statement** between whoever operates the platform and whoever runs an
  outpost: who answers for the policy, the upgrades, the logs, and for what a tool is allowed to return.

The stories above advance on fakes until those exist.
