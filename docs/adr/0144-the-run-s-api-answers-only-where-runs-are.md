# 0144 — The run's API answers only where runs are

- **Status**: proposed, 2026-11-11; S65-02 implements it
- **Concerns**: `/api/v1/internal/*` (`routers/internal.py`, `routers/agent_session_pods.py`, the
  relay's route of `routers/outbound_proxy.py`), `internal_api_guard.py` and `main.py`, the API chart
  (`httproute.yaml`, `internalApiGuard`, `publicHostnames`, `runDoor`), `global.internalApiUrl`,
  `compose.yaml`; builds on [ADR 0115](0115-the-agent-never-holds-the-run-token.md),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) and
  [ADR 0072](0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md)

## Context

`/api/v1/internal/*` is the run's own API: a runner reads its input and posts its events and result
there, the run's tool server relays its calls, a hosted session's pod takes its turns, the per-run
egress proxy reads a run's network, the outbound relay reads an organisation's proxy. Each caller
holds a token of its own (a run token, an egress token, a session token, a relay token), and that
token is the only thing those routes check.

The chart routes the whole `/api` prefix of the API's public hostnames to the API, under both of its
entry forms (`Ingress` and `HTTPRoute`). The run's API therefore answers on the same hostnames as the
console and the webhooks, to anyone on the network those hostnames are published on. The console's
own relay of `/api/v1` is a second way in. That exposure serves nobody and costs something:

- the tokens are scoped and expire (a run's with its stage, a relay's after days), but one that
  leaves its pod can be presented from anywhere for as long as it lives;
- the routes are not rate-limited: the limiter covers the routes *without* a principal;
- no caller comes that way in an installation whose runs are in the cluster.

Runs call the address the workers hand them, `CHOREGOS_INTERNAL_API_URL`, one value for every run of
an installation:

| Installation | Callback address | `Host` the API sees |
| :-- | :-- | :-- |
| chart, agent stages in the release namespace (`agent_sandbox`, the default; the local bench) | `http://choregos-api:8000` | `choregos-api:8000` |
| chart, runner namespaces of their own (`k8s_job`, `tekton`) | the Service's full name | `choregos-api.<namespace>.svc[.cluster.local]:8000` |
| `docker compose` (`local_docker`) | `http://api:8000` | `api:8000` |
| a development machine (`make api`, `make worker`) | `http://localhost:8000` | `localhost:8000`, which is also the API's public URL there |
| `make demo` | none: in memory | — |
| an executor outside the cluster (`aca`) | whatever the operator wrote in `global.internalApiUrl`: today, a public hostname of the API | that hostname |

The last row is legitimate, and the fourth shows that "public" and "where runs call" can be the same
name. On 2026-09-27 a wrong callback address ended every run of an installation at its first call
(`hosts_to_reach_directly` keeps the account). A guard on these routes can do exactly that again.

## Decision

1. **One guard, by path, in the API.** `internal_api_guard.py` is a middleware `main.py` installs
   innermost. It reads every request under `/api/v1/internal`, whatever router declares the route: the
   two internal routers, the relay's route, an extension's. No handler checks anything itself.
2. **It decides on the name the request arrived under.** A request that arrives under a *public*
   hostname of the API is refused, unless that hostname is a run door (4). Any other name is served
   as before: the Service, a compose service, `localhost`, an address.
   - *Public* is declared, never guessed: the hosts of `CHOREGOS_PUBLIC_URL`, `CHOREGOS_API_URL` and
     `CHOREGOS_CORS_ORIGINS`, and `CHOREGOS_PUBLIC_HOSTS`, where the chart writes the hostnames of its
     own route and `publicHostnames`. Ports and case are not compared; `*.` names every sub-domain.
   - *Arrived under* is the `Host` header and every `X-Forwarded-Host` entry. The second is read
     because a reverse proxy may rewrite `Host` and say where the request came in with that header:
     the console's relay does. One public name among them refuses the request. A name in
     `X-Forwarded-Host` never opens anything, a run door's included: a client that writes the header
     can only get its own request refused.
3. **A refusal is the router's own 404**, the same bytes as for a path that does not exist, sent
   before any token is read: the answer does not say whether a route, a run or a token exists.
4. **A run door for runs that call back from outside.** `CHOREGOS_RUN_DOOR_HOSTS` lists hostnames the
   run's API answers under even when they are public. `choregos-api.runDoor.hostnames` sets it and
   renders a route of its own, `choregos-run-door`, that serves `/api/v1/internal` and nothing else,
   under both entry forms, with its own annotations: that is where an operator restricts the source
   addresses or the rate. The public route is unchanged. A door hostname that is also in
   `ingress.hostnames` fails the render.
5. **Three modes, `warn` by default.** `CHOREGOS_INTERNAL_API_GUARD` (`choregos-api.internalApiGuard`)
   is `off`, `warn` or `enforce`. `warn` refuses nothing and logs `internal_api.would_refuse` for each
   request `enforce` would refuse; `enforce` logs `internal_api.refused`. A line carries the host that
   matched, the header it was read from and the first path segment after `/internal`, never a token.
   No preset of the chart enforces: an installation switches after a release in `warn` has shown that
   log empty of its own callers.
6. **Never the address runs are sent to.** The API is told the workers' callback address (the chart
   gives it the same `CHOREGOS_INTERNAL_API_URL`; compose shares it). When that address is itself a
   public hostname and no run door covers it, `enforce` is not applied: the guard keeps warning, and
   says once at start-up (`internal_api.guard_not_enforced`) what to configure. Without the address,
   the API assumes its own public URL, which holds `enforce` back as well.
7. **A setting that cannot work stops the start or the render**: a mode outside the three, a door or
   a public hostname written as a URL or with a port, any of the chart-owned names in
   `global.extraEnv`.

## Alternatives rejected

- **A test on the source address** (serve the run's API to the pod network only). Behind an ingress
  that is not a declared trusted proxy, a public request arrives from the ingress pod, inside the pod
  network, like a runner. With the chart's default trusted ranges the address is read from
  `X-Forwarded-For`, which runner pods can write (`rate_limit.py` says so), and clients on a private
  network look like pods. Runs outside the cluster have addresses no list can hold.
- **Removing `/api/v1/internal` from the route.** An `Ingress` routes by prefix and cannot subtract a
  sub-path portably; doing it per controller is an annotation per controller. It would not cover the
  console's relay, nor a route made outside the chart. The guard covers every way into the process.
- **A list of the names runs may use**, everything else refused. The forms of an in-cluster address
  are open-ended (short name, namespaced, fully qualified, an address, a compose service), and one
  missing form ends every run at its first call. Refusing the declared public names fails the other
  way: a name nobody declared stays served.
- **Opening a door from `X-Forwarded-Host`.** A client writes that header.
- **The run's API on a port of its own**, which no public route reaches. The stronger wall, and the
  one this decision leaves open: it moves the address every run calls, in every installation, at
  once.
- **`enforce` by default.** The guard cannot refuse the address runs are sent to, but an installation
  may have callers of its own on a public hostname (a relay configured by hand, a proxy in front of
  the Service). `warn` shows them first.

## Consequences

- With `enforce`, the run's API no longer answers on the console's and the API's public hostnames. An
  upgrade changes nothing until then: the default logs, and the exposure stays until the operator
  switches.
- An executor outside the cluster needs a run door before `enforce` means anything; until then the
  guard says so at start-up and refuses nothing. The door is as public as the operator makes it: its
  route is where to restrict it, and the tokens remain what its routes check.
- The outbound relay and any other caller an operator deployed must reach the API by the Service or by
  a run door; `warn` names them (`path_prefix`).
- A run keeps the address it was started with. Moving the callback address and enforcing in one
  roll-out would refuse the runs in flight: the deployment guide orders the two steps.
- It is not a wall against a caller inside the cluster, which uses the Service's name: the tokens and
  the network policies are. Nor against a hostname routed to the API that the deployment never
  declared (`publicHostnames`), nor behind a proxy that rewrites `Host` without `X-Forwarded-Host`.
- Workload identity for runs, and a listener of their own, stay open.
