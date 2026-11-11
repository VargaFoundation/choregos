# 0148 — The outpost door is a third door, with its own contract

- **Status**: proposed, 2026-11-11
- **Concerns**: a new route at the root of the API process, `/outposts/v1` (a new package
  `apps/api/src/choregos_api/outposts/`, mounted beside the two doors in `main.py`); its contract
  `packages/contracts/outpost-door.yaml`; a new table `outpost_calls`; a second row-level security setting,
  `app.current_outpost` (`db/session.py`); the event relay (`events.py`, `PostgresRelay`);
  `choregos_adapters.sealing`; the key wrapper of `secret_store.py`; the door's signing key, a secret of
  the installation, and the `door_keys` an outpost's policy file pins; the API chart (a Deployment and a
  route of its own); the boundary test `tests/licences/test_mcp_gateway_boundary.py` (`DOOR_DIRS`,
  `DOOR_ROUTES`). When accepted, amends
  [ADR 0105](0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md) §1 (its doors "offer no
  passthrough": the conditions under which a door that serves assignments is not one). Builds on
  [ADR 0001](0001-frozen-contracts.md), [ADR 0027](0027-noisy-neighbours-at-admission-and-quota.md),
  [ADR 0030](0030-an-mcp-door-for-external-clients.md),
  [ADR 0078](0078-governed-workflows-as-a2a-skills.md),
  [ADR 0085](0085-the-approved-row-is-the-outbox-of-a-governed-action.md),
  [ADR 0091](0091-the-search-engines-reach-the-proxy-through-a-relay.md),
  [ADR 0103](0103-secret-keks-sit-behind-a-key-wrapper.md) and
  [ADR 0147](0147-an-outpost-only-dials-out.md); plan `docs/plan/PLAN-2026-11-11-outposts.md` (stream S65)

## Context

An outpost (ADR 0147) opens every connection itself and holds a key pair, not a token. What it needs from
the platform is neither tools nor tasks: it needs its *assignments*, the calls its organisation's
connectors route to it ([ADR 0149](0149-a-tool-may-live-at-an-outpost.md)). What the code offers and
constrains today:

- **Two doors**, at the root of the API and out of the OpenAPI document: `/mcp` (ADR 0030) and `/a2a`
  (ADR 0078), mounted in `main.py`. Both speak a protocol someone else defined, to callers who hold a
  `chg_` token or an OAuth token. Neither has a contract file in `packages/contracts`: their routes are
  listed as outside the contract (`tests/web/test_api_routes_have_a_client.py`, `MACHINE_ONLY_ROUTES`).
- **The boundary test freezes the doors**: the directories that are doors (`DOOR_DIRS`), what they may not
  import, in their own lines and in their whole import closure (`FORBIDDEN_MODULES`: the broker, the
  gateway services, the MCP client, `choregos_adapters.build`, the plugin servers), and their routes by
  verb and path (`DOOR_ROUTES`).
- **The daemon lives in someone else's network**, upgraded on their schedule: it lags the platform, by
  weeks. Between it and the door there is often an outbound proxy of the organisation that terminates TLS
  to inspect it.
- **The API has no shared store**: `rate_limit.py` keeps its windows "in memory, per client address and
  per replica", and says "There is no shared store, by decision".
- **The routes a pod calls set the all-organisations scope**, because their token names one run or one
  session (`deps.py`, `run_claims`; `routers/agent_session_pods.py`, `session_pod_record`).
- **One long poll exists.** `get_next_turn` (`routers/agent_session_pods.py`) commits its own transaction
  and then looks in a short one of its own every 0.2 s, for 30 s at most: it holds no transaction while it
  waits. It is sized for one pod per session, inside the cluster.
- **Every replica already listens.** `PostgresRelay` (`events.py`) holds one `LISTEN` connection per
  replica and republishes what it hears into the process.

## Decision

1. **A third door, `/outposts/v1`, that serves assignments and never MCP.** It is mounted at the root,
   beside `/mcp` and `/a2a` and out of the OpenAPI document like them, only when `CHOREGOS_OUTPOSTS` is on.
   An outpost lists no tool of the platform, calls none and speaks no JSON-RPC to it: it takes the calls
   routed to it, and answers them.
2. **Its own versioned contract.** `packages/contracts/outpost-door.yaml`, with the schema of the
   outpost's policy file and examples, under ADR 0001: a change is a `contract-change`. The two other
   doors need no contract of ours because their protocols are public standards. This protocol is ours, and
   its other implementer is a daemon nobody at the platform upgrades.
3. **Four verbs.**

   | Verb | Carries | Answers |
   |:--|:--|:--|
   | `POST /outposts/v1/enrol` | an enrolment token, the instance's public keys, facts about it | a *pending* instance and the fingerprint to compare (ADR 0147 §4) |
   | `POST /outposts/v1/poll` | signed: the instance's sequence, how long it waits, what it is running | calls to make, each signed by the door (13), cancellations, the server's time, the minimum version, a stop flag |
   | `POST /outposts/v1/calls/{id}/answer` | signed, sealed, bounded | recorded once: idempotent on the call, the instance and the epoch |
   | `POST /outposts/v1/calls/{id}/refuse` | signed: a refusal code from a closed list, which does not reveal the policy | recorded once |

   Four is a constraint, not an accident. Every verb added is one that every daemon in the field must
   learn, or that the door must serve two ways for a while. The protocol version is negotiated at every
   poll; the door announces the window it serves (a current and a minimum version); a daemon below the
   minimum gets a clean refusal that names the minimum, and no assignment. A new need enters the poll's
   answer as a field an older daemon ignores before it ever becomes a verb.

   An enrolment token enrols one instance, once. A signing key that is still pending at the token's own
   outpost enrols again as the same instance, under a new token: a daemon whose first answer was lost on
   the way keeps its keys. A key that was ever approved or revoked, or that is pending at another outpost,
   is refused: a revoked key does not come back.
4. **A request is authenticated by a signature that binds it.** Every request after `enrol` carries a
   token signed with the instance's Ed25519 key (EdDSA) that binds the method, the URL, a digest of the
   body, an `iat` at most 60 seconds old, a nonce the server issued and a per-instance sequence number. A
   refusal for a stale `iat` carries the server's time, so a daemon whose clock drifts learns it.
   - The enrolment is signed too, with the key it enrols: the same token, without a nonce or a sequence,
     since neither exists yet. It proves the enroller holds the key, and it ties the sealing key to the
     signing key whose fingerprint a person compares (ADR 0147 §4). Without it, a proxy on the path could
     swap the sealing key under a fingerprint that still matches, and read every call.
   - The nonce is issued by the server and needs no stored state: an authenticated timestamp the door can
     check alone (the server-provided nonce of RFC 9449 §8).
   - The sequence is kept on the instance's row. A request whose sequence is not above the row's is a
     replay, at whichever replica it arrives.
   - *Rejected: mutual TLS.* An organisation's outbound proxy often terminates TLS to inspect it; a client
     certificate does not cross it, and the door would be unreachable exactly where an outpost is needed.
   - *Rejected: a `jti` cache.* A replay cache needs a store every replica shares, and the API has none,
     by decision (`rate_limit.py`): one replica's memory lets a replay through at the next. A row is what
     the replicas do share.
5. **Requests and answers are sealed end to end.** A call's arguments are sealed to the claiming
   instance's X25519 key, and its answer is sealed back, with `choregos_adapters.sealing` (`seal`,
   `open_sealed`: an ephemeral X25519 exchange, HKDF-SHA256, ChaCha20-Poly1305), the call's id as
   associated data where a run's id is today. A proxy that terminates TLS reads the envelope (ids, sizes,
   timing) and nothing of the arguments or of the result. The key an answer is sealed to reaches the
   outpost inside a sealed request or at enrolment, never in a field a proxy could rewrite; the contract
   fixes the envelope. Sealing hides a call; it does not say who sealed it. That is decision 13.
6. **One organisation, never all of them.** The door resolves a signature to one instance of one outpost
   of one organisation, then sets `app.current_orgs` to that organisation and `app.current_outpost` to that
   outpost. It never sets the all-organisations scope the pod-facing routes set. The outposts' tables are
   under the organisation's forced row-level security, like every tenant table, and their policies also
   require the outpost setting, for reading and for writing; it fails closed like the first one: a session
   that names no outpost sees none of their rows, and the platform's own paths name the outpost their
   connector routes to. The door has no route that lists anything, and whatever is not this outpost's
   answers 404, never 403: the answer does not say whether a call, an instance or an outpost exists.
7. **The long poll.** One poll per instance at a time: a second poll under the same key is refused as a
   conflict and raises an alert, because two holders of one key is what a stolen key looks like. Lanes are
   multiplexed in the answer (calls to make, cancellations, whatever a later capability adds), so one
   connection per instance is all an outpost holds. A poll waits 25 seconds at most, with jitter, below the
   idle timeout of common proxies. While it waits it holds no transaction, on the model of
   `get_next_turn`: each look is a short transaction of its own. It is woken by the `LISTEN` relay every
   replica already runs (a notification leaves with the commit of the row it announces, never before:
   ADR 0085), and looks again every 5 to 10 seconds whatever it heard, because a notification can be lost
   across a reconnection.
   *Rejected: a WebSocket or a server-sent stream.* Long-lived streams are what inspecting proxies break
   first.
   *Rejected: looking every 0.2 s, as the session poll does.* That is sized for one pod per session, not
   for every instance of every outpost.
8. **Its own Deployment and pool, under a hostname of its own.** The chart can run the door as its own
   Deployment: the API image, serving this router only, with its own bounded connection pool
   (`db_pool_size`, `db_max_overflow`). Polls waiting by the hundred then take no worker and no connection
   from the console and the runs. It is exposed under its own hostname: the one name an outpost's firewall
   opens, and the one an operator can rate-limit or restrict by source apart from the console's. The
   precedents are an entrypoint of the API image run as its own Deployment: the relay of ADR 0091, which
   an operator deploys, and the egress proxy, which the chart renders
   (`charts/choregos/templates/egress.yaml`). Neither holds a database credential; this Deployment does,
   and that is new. Where the door is not split out, the API serves it.
9. **Bounds a hostile outpost meets.** A body above the cap is refused before it is parsed, as the MCP
   door refuses one above 1 MiB (ADR 0030 §2). A lease has a maximum lifetime, whatever the daemon asks
   for. An answer is accepted only from the instance that claimed the call, under the epoch it was claimed
   in, for that call's id, once. A refusal code is one of a closed list. A facts field at enrolment is
   bounded and stored as data, never interpreted.
10. **Quotas and switches.** Calls in flight and calls per minute are capped per outpost and per
    organisation, where a call is admitted, in the line of ADR 0027: a noisy neighbour is bounded at
    admission and by quota, not by a queue or a pool per organisation. There is a switch per outpost
    (`pause`: nothing more is handed to it, and the poll's stop flag tells its instances) and a global one
    (`CHOREGOS_OUTPOSTS`, ADR 0147 §12).
11. **Secrets at rest.** A call's row holds references and digests. Its arguments wait for their claim
    encrypted under the key wrapper of ADR 0103 and are deleted when the call completes; the digests of the
    arguments and of the answer stay, as a `tool.called` event keeps digests and no payload. Nothing of the
    outpost's side is stored, because there is nothing to store: the platform has no URL and no credential
    of it (ADR 0147 §7).
12. **Why this door does not make Choregos a gateway** (amends ADR 0105 §1). ADR 0105 says the doors
    offer no passthrough. A door that hands calls to another network could become one; it does not, under
    three conditions, each of which is a test:
    - **it serves assignments, never MCP.** No `tools/list`, no `tools/call`, no JSON-RPC: nothing a third
      party could use to reach a server through the platform. Its only caller is the place where the
      server lives;
    - **it has no route that names a connector or a server.** Its four routes join `DOOR_ROUTES`, frozen by
      verb and path, parameter names included; the one path parameter is a call's `{id}`;
    - **it imports no broker module.** Its directory joins `DOOR_DIRS`, so its files and their whole import
      closure are refused the broker, the gateway services, the MCP client, `choregos_adapters.build` and
      the plugin servers. It meets a call only as a row of `outpost_calls`. `choregos_adapters.sealing` is
      not one of the refused modules and imports none of them (checked by importing it alone).

    The broker's half of the same route is ADR 0149's.
13. **A call is signed by the door.** Sealing (5) hides a call from whoever is not its recipient. It does
    not say who sealed it: anyone who knows a public key can seal to it. Whoever terminates the daemon's
    TLS, an organisation's inspecting proxy for one, knows the instance's sealing key, which crossed it at
    enrolment. It could hand the daemon a call of its own, and read the answer through an `answer_key` of
    its own: an execution channel into the outpost's network for a party this design never meant to trust.
    So a call the door hands over is signed by the door, and an outpost makes only calls signed by a key
    its own policy file pins.
    - **The key.** The door holds an Ed25519 signing key, and the next one during a rotation. A public key
      is written as the other keys of the contract are: the unpadded base64url of its 32 raw bytes.
    - **The pin.** The outpost's policy file has a required `door_keys`: one to four distinct keys. It is
      the only place the pinned set lives: nothing the door sends, at enrolment or later, adds a key to it
      or changes it. A policy file without it is invalid, so the daemon does not start: an outpost that
      cannot check who orders it refuses to serve (ADR 0101). The key itself is pinned, not a fingerprint:
      an Ed25519 key is as short as its digest, and no key then has to travel with a call.
    - **The signature.** Each call in a poll's answer carries a compact JWS (`alg` `EdDSA`, `typ`
      `choregos-outpost-call+jwt`, `kid` the signing key) whose claims are the call's id, the claiming
      instance's id and `sealed_sha256`, the digest of the envelope exactly as carried, and nothing else.
      There is no `iat`: a call is as fresh as the deadline inside its envelope, which the digest covers.
    - **What the daemon does**, before it opens the envelope and in this order: the `kid` is one of the
      pinned keys; the signature verifies under it; the call is the envelope's; the instance is its own;
      the digest matches. If one fails it does not open the envelope and does not call its server. It
      writes a line to its audit log and refuses the call with one code, `signature_refused`, which names
      the class and never the check. A forged call's refusal meets a 404 at the door, which is harmless; a
      real call refused this way is how the platform learns that an outpost does not pin the key in
      service.
    - **Replays.** An outpost decides once per call id. Its journal (ADR 0149 §5) holds the first thing it
      decided for an id, to make the call or to refuse it, and every later envelope under that id gets the
      same: a copy that does not verify cannot have a call that is under way refused, and a refused id is
      never made. A signed call replayed to the same instance is therefore answered from the journal, its
      server not called, and refused once its deadline is behind; the journal keeps an id at least until
      then. The outpost judges a deadline by its own clock, because the server's time is not signed.
    - **What is not signed**: the rest of a poll's answer (the stop flag, the cancellations, the version
      window, the server's time), the enrolment's answer and every problem. Whoever sits on the path can
      stop, starve or mislead a daemon about versions and time, as it can by dropping packets. It cannot
      make it call anything, nor read an answer.
    - **Rotation is an overlap the outpost's operator takes part in.** The platform shows the next key:
      the management API returns the keys to pin with an outpost and with its enrolment token, never the
      door itself. Operators add it to `door_keys`; then the door signs with it. An outpost that did not
      add it refuses with `signature_refused`, visibly. No message of the protocol rotates a pinned key.
    - *Rejected: trusting the TLS connection.* It ends at the proxy.
    - *Rejected: a key learned at enrolment.* That is trust on first use, across the same proxy.
    - *Rejected: a key carried in the enrolment token.* The pinned set must be the operator's to read and
      to change, in the file the control plane cannot write, and every instance of an outpost shares that
      file; a token is used once, by one instance.

## Alternatives rejected

- **The outposts' routes under `/api/v1/internal`.** Those routes are the run's own API: callers the
  platform started, tokens it minted, the all-organisations scope. An outpost is another party's machine.
- **MCP across the door**, in either direction: the outpost as an MCP client of a server that would hand
  out calls bends a protocol that has no such notion; the platform as an MCP client through a reverse
  tunnel is the tunnel ADR 0147 refuses.
- **A message broker, with a queue per outpost.** One more stateful dependency in every installation, for
  a notification PostgreSQL already carries.
- **A bearer token per instance.** It can be replayed by whoever reads it once, an inspecting proxy
  included; a signature over the request cannot.

## Consequences

- A contract change (S65-10), a migration with its row-level security (S65-11), the door and its tests
  (S65-12), a chart change (S65-16). `"/outposts/v1/*"` joins `MACHINE_ONLY_ROUTES`.
- `PostgresRelay` republishes only what parses as a platform event, on one channel (`events.py`,
  `_receive`). Waking a poll therefore needs an event type in the event contract or a second channel on
  the same connection; S65-12 chooses, and the notification carries ids only. On SQLite there is no relay
  (`main.py`): the periodic look is what serves a local bench.
- A compatibility window is a promise to operators: the door keeps serving the previous version's
  daemons, and its conformance test plays their fixtures.
- An inspecting proxy still sees who talks, when and how much. The operator's guide says so.
- The door holds one more secret of the installation, its signing key (13): the chart generates it
  (S65-16). Rotating it is an overlap the operators of outposts take part in: the next key is shown, they
  pin it, and only then does the door sign with it.
- This record proves nothing: it is the decision and its guard.
