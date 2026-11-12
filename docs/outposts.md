# Outposts

For whoever operates the platform, and for an organisation's administrator. An outpost lets the
tools of a connector live in a network the platform cannot connect to: a production network, an
internal tracker or forge, a database behind a firewall whose rule is "no inbound". This page says
when to want one, what it changes, what it does not give, and where each gesture is described. It
holds no table of settings and no walk-through: each section points to the page that does. The
person who runs an outpost in their own network has a guide of their own,
[Running an outpost](outpost-operator.md).

The decisions are [ADR 0147](adr/0147-an-outpost-only-dials-out.md) (the concept),
[ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) (the door) and
[ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md) (the connector), all three still proposed.
Outposts are off unless a deployment turns them on, and **nothing here has run against a real
outpost on a real network yet**: the [last section](#what-is-not-proven-yet) says what has been
tested and what has not.

## Before an outpost: your organisation's own MCP gateway

If the servers you want agents to reach are already behind an MCP gateway your organisation runs,
and the platform can reach that gateway, **you need no outpost**. Choregos consumes the gateway as it
is ([ADR 0105](adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md)): set it up as
[Integrations](integrations.md#your-organisations-mcp-gateway-adr-0105) describes, and nothing below
applies to you. Look there first:

- **No program of the platform runs in your network.** The gateway is a product you already operate,
  behind an entrance you already have: a DMZ, or an outbound tunnel of your own.
- **It promises more for a write.** A write through the gateway is a governed action like any
  other. Through an outpost, a write is delivered at most once and may end in doubt
  ([Integrations](integrations.md#a-tool-server-at-an-outpost-adr-0149)): weaker, and never implied.
- **The upstream credentials stay on your side either way**: at the gateway in one case, in the
  outpost's policy file in the other.

An outpost is the same service for an organisation that has no gateway where its tools are, or that
will not expose one: shipped by the platform, installed and run by the organisation. ADR 0147 weighs
two more answers and says when each is the right one: a second cluster of your own, which a private
network already joins to the platform's; and a third party's agent, called over A2A, when a black
box is accepted.

## What an outpost is

An outpost is a place of one organisation that a person enrols. One or more **instances** of the
daemon `choregos-outpost` run there, each with keys it made itself. Every connection between an
outpost and the platform is opened by the outpost, to one hostname, on port 443: the outposts' door.
The platform never connects to an outpost, stores no address of it and needs no route to it.

In this version an outpost is for one thing, a connector's tool calls:

1. An administrator declares a connector of type `mcp-outpost`. It names the outpost and an
   **alias**: no URL, no secret.
2. An agent calls one of the connector's tools. The platform decides the call as it decides any
   other (below), then writes it where the outpost can claim it, instead of opening a connection.
3. An instance of the outpost takes the call at its next poll, sealed to that instance and signed by
   the door. It checks the signature against the keys its own policy file pins, applies that file
   (the alias, the tool's name, writes, ceilings), calls the MCP server the alias names there, with
   the credential it holds for it, and sends the answer back, sealed.
4. The platform bounds and screens the result and hands it to the agent, as for any connector.

The address behind an alias and the credential that opens it exist in the outpost's policy file
only. The platform cannot write that file, and nothing the door sends changes it.

## What stays where it was

The transport of a tool call changes. What governs it does not:
`apps/api/tests/test_broker_via_outpost.py` plays the same call through a direct connector and
through an outpost, and compares what comes back.

- **Decided before anything leaves**, by the broker, at the platform: the tools the agent's version
  selects; each operation's policy and groups, where a discovered tool is born `forbidden`; its
  argument schema; the grants that bind its arguments; and the approval of whoever must approve.
  What the platform refuses writes nothing an outpost could claim.
- **Applied when the answer comes back**: the bound on the result, the injection guard, the ledger
  line `mcp:<connector>` and the digests of the `tool.called` event.
- **Journaled**: each call of a project is a line of the network activity, under
  `outpost:<name>/<alias>`. The platform opened no connection, so the organisation's allowed hosts
  and its per-host rate have no host to judge; what bounds the call instead is the door's quotas
  (calls in flight and calls per minute, per outpost and per organisation) and the outpost's own
  ceilings.

The outpost decides nothing the platform decides. It may refuse what its own policy refuses, and
the caller then reads a typed refusal that names the outpost
([Integrations](integrations.md#a-tool-server-at-an-outpost-adr-0149) lists them).

## What an outpost does not give

- **Data residency.** A tool's arguments leave the platform for the outpost, and its result travels
  back: it is bounded and screened at the platform, handed to a model through the model gateway, and
  kept where the platform keeps what a tool returned. What an outpost keeps away from the platform
  is a network's addresses and credentials, not the data that flows. A result that must not leave
  that network must not be a tool's answer. The operator's guide says so
  [before anything else](outpost-operator.md#an-outpost-does-not-give-data-residency).
- **Protection against whoever holds the control plane, beyond the outpost's own policy.** A
  compromised control plane, or whoever holds the door's signing key, can ask an outpost for what its
  policy file allows, and no more: it holds no address and no credential of the outpost's side, and
  it cannot add an alias, widen a pattern, turn writes on or pin another key. The outpost bounds
  what can be asked of it; it does not remove it
  ([Security](security.md#what-an-outpost-protects-against-and-what-it-does-not)).
- **A tunnel.** There is no generic HTTP relay into the outpost's network: the platform names an
  alias and a tool, never a host. An internal API is reached as a tool of an MCP server the
  outpost's operator runs.
- **Servers placed by the platform.** The platform creates no workload at an outpost and sends it no
  secret. Whoever runs the outpost deploys what runs there.

## Switching outposts on

The platform's operator does this once for the deployment.
[Deployment](deployment.md#outposts-adrs-0147-0149) holds every setting and every value of the
chart; what there is to arrange:

- **The switch**, `CHOREGOS_OUTPOSTS` (the chart's `global.outposts.enabled`), off by default. Off,
  the door is not served and creating an outpost is refused. It is also the one switch that stops
  every outpost at once.
- **A hostname of the door's own**, with its DNS record and its certificate: the one name an
  outpost's firewall opens. Its route needs an idle timeout above the 25 seconds a poll waits. The
  chart writes that for an HTTPRoute and for ingress-nginx; an Ingress controller other than nginx
  needs its own equivalent.
- **The door's signing key**, a secret of the installation whose public half every outpost pins.
  Under a GitOps tool that renders without reading the cluster, create its Secret yourself: the
  chart cannot read back a key it drew.
- **The key the deployment seals secrets under**: a call's arguments wait under it until an instance
  claims them, and without it nothing is handed to an outpost.
- **The alert on a second poll under one key**, which is what a stolen key looks like.

## Creating an outpost, enrolling and approving an instance

An organisation's administrator does this (the right is `outpost:manage`), in **Administration →
Outposts** or with `choregos outposts`. [Usage](usage.md#4-write-the-policy) describes the page and
the commands, under "Outposts"; the [CLI reference](cli.md) has each command.

1. **Create the outpost**: a slug and a name. It has no instance yet.
2. **Issue an enrolment token** for one instance. It is shown once, with what the outpost's operator
   needs: the door's origin, the door's keys to pin (`door_keys`) and the command to run. A token
   enrols one instance, once, and lives fifteen minutes at most.
3. **The operator enrols the instance** on their side ([their guide](outpost-operator.md#enrolment)).
   The daemon makes its keys, presents the token and prints the **fingerprint** of its signing key.
   The instance is born *pending* and is handed nothing.
4. **A person approves it** in the console, by comparing the fingerprint shown there with the one the
   daemon printed. The approval is taken from a person's session that carries a recent sign-in,
   never from an API token: the CLI's `approve` is refused by design. A stolen enrolment token
   therefore enrols an instance nobody approves.

Each further instance of an outpost (availability, a rolling upgrade) takes a token of its own and
is approved on its own.

## Declaring a connector at an outpost

A connector of type `mcp-outpost` is declared with the outpost's name, the alias of an endpoint in
that outpost's policy file, and a timeout.
[Integrations](integrations.md#a-tool-server-at-an-outpost-adr-0149) says what it changes for agents
and what it can refuse; the walk-through is the procedure
[Connect an MCP server that lives at an outpost](procedures/connector.mcp.mcp-outpost.md).

- The alias is agreed with the outpost's operator: it is a name in their file, and the platform
  never learns what is behind it. In the console's connector form the outpost and the alias are
  chosen from what the organisation's outposts announce; an alias is typed only while no instance
  of the outpost announces one yet.
- An agent's access tab names, for each tool reached through an outpost, the outpost and the alias,
  and says whether it is online now (ADR 0149 §10).
- The connector may be declared before an instance runs; discovery then answers that the outpost is
  offline.
- Discovery returns the tools the outpost's policy lets through, each born `forbidden`. An
  administrator opens the reads the agents need, one by one.
- An outpost that still has connectors is not deleted.

## Reading its calls

- **Administration → Outposts**, the outpost's page, *Recent calls*; or
  `choregos outposts calls <slug>`, with `--state refused` for the refusals. Each call has its state
  and, for a refusal, the outpost's code. Digests only: the platform keeps no arguments and no result
  of a call there ([Usage](usage.md#4-write-the-policy)).
- **The network activity** of the organisation: a line per call of a project, under
  `outpost:<name>/<alias>`, `allowed`, `denied`, `rate_limited` or `unreachable`.
- **The cost ledger and the run's events**, as for any connector. A call that reached no server is
  not booked.
- **The organisation's audit log**: who created, paused, resumed, revoked or deleted an outpost, who
  issued a token and who approved an instance; and `outpost.poll_conflict`, the event behind the
  alert.
- **The outpost's own audit log**, which its operator keeps: a record of the same calls, independent
  of the platform's. The two can be compared by call id and by the digest of the arguments
  ([the operator's guide](outpost-operator.md#the-local-audit-log)).

## Pausing, revoking, deleting

- **Pause** holds a healthy outpost: nothing more is handed to it, its instances are told at their
  next poll, and what they hold may finish. **Resume** undoes it. This is how an outpost is held or
  retired.
- **Revoke an instance** answers a key that may be stolen. It is final and holds from the
  instance's next request. It moves the outpost's epoch: nothing in flight at the outpost is taken,
  from this instance or another. A revoked key does not come back for as long as its outpost exists.
- **Revoke the outpost** revokes every instance of it, and is final. Connectors that route to it are
  refused until they are routed elsewhere.
- **Delete** removes a revoked outpost with its instances. It is refused while the outpost is not
  revoked, and while connectors still route to it.

The commands and the buttons are in [Usage](usage.md#4-write-the-policy); what a revocation changes
at the door is in [Security](security.md#the-outposts-door).

## Rotating the door's key

An outpost makes only the calls signed by a key its policy file pins, and no message of the platform
changes that list. A rotation is therefore an overlap that the operators of outposts take part in:
the platform shows the next key beside the one that signs; each operator adds it to their file; only
then does the door sign with it; the operators drop the former key afterwards. An outpost that did
not add the next key refuses every call signed with it (`signature_refused`), visibly.
[Deployment](deployment.md#outposts-adrs-0147-0149) gives the steps on the platform's side, with and
without the chart; [the operator's guide](outpost-operator.md#the-doors-keys-pinning-and-rotation)
gives the operator's.

## What is not there yet

- **Writes, from the console.** A write through an outpost is delivered at most once and may end in
  doubt ([ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md) §5-6;
  [Integrations](integrations.md#a-tool-server-at-an-outpost-adr-0149)). It is opened only when its
  operation's rule says one of two things: that the tool takes the platform's idempotency key
  (`platform_arguments: ["action_key"]`), or that an in-doubt outcome is accepted
  (`in_doubt_accepted: true`). The console's operation editor does not offer these two statements
  yet: an administrator gives them with the policy, through the API
  (`PATCH /orgs/{org}/connectors/{name}/operations/{operation}`); without either, the change is a 422
  that names both.
- **Closing an action in doubt.** No route closes it by hand: a person checks the system behind the
  outpost and records what they found where the team keeps such records
  ([Usage](usage.md#4-bis-governed-actions)).
- **Runs at an outpost.** A whole run executing at an outpost is not decided: ADR 0149 §12 lists
  what would have to be true first. `tools` is the only capability an instance announces.
- **A person's identity.** A call reaches the server under the credential the outpost holds for the
  alias. A need that must be called as the person cannot be bound to such a connector.
- **Two details of the deployment**, said in [Deployment](deployment.md#outposts-adrs-0147-0149):
  the door's own process exposes no metrics when it is split out, and the two caps per minute are
  not values of the chart.

## What is not proven yet

Each half is tested against the door's contract (`packages/contracts/outpost-door.yaml`) and against
a scripted stand-in for the other: the door against a scripted daemon, the daemon against a scripted
door, and both against the contract's vectors. The connector is tested through the real door, with
that scripted daemon. Not proven:

- **the real daemon and the real door together**: a tool call end to end is the story S65-19;
- **anything on a cluster or on a real network**: the two charts are rendered and checked as
  documents; no route, no proxy, no firewall and no network plugin has been in the path;
- **that a connection from the platform's network to the outpost's fails** where a network plugin
  enforces policies, which is the point of the design and what only such a cluster can show;
- **a rotation of the door's key** with real outposts.

Until S65-19 has run, an outpost is built and tested in parts; it has not been operated.
