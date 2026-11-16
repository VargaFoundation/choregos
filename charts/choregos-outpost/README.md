# choregos-outpost

The chart of the daemon `choregos-outpost`: what whoever runs an outpost installs in **their own**
cluster ([ADR 0147](../../docs/adr/0147-an-outpost-only-dials-out.md)). It is not a subchart of the
platform's chart and shares no value with it. The daemon's own page is
[`packages/outpost/README.md`](../../packages/outpost/README.md); this one says what the chart
renders and what it needs from you.

An outpost is a place an organisation enrols, which only dials out: it polls one hostname of the
platform (the door), makes the calls its own policy file allows against MCP servers of your
network, and answers. Nothing dials it. **It does not give data residency**: a tool's result is
sealed to the platform and sent there.

## What you need first

From the platform, with the outpost its administrator created and an enrolment token:

- the door's origin, for `policy.control_plane` (`https://outposts.example.com`);
- the door's public keys, for `policy.door_keys`: the only keys whose calls this outpost makes
  ([ADR 0148](../../docs/adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) §13);
- the enrolment token: single use, valid 15 minutes.

In your cluster: a StorageClass for a small volume per instance; one firewall rule, outbound, to
the door's hostname on port 443 (or to your outbound proxy); and a network plugin that enforces
NetworkPolicies, if you want the one this chart renders to hold.

## Install

```bash
kubectl create namespace outposts
kubectl -n outposts create secret generic factory-floor-enrolment --from-literal=token=<the token>
helm install factory-floor oci://ghcr.io/vargafoundation/charts/choregos-outpost \
  --namespace outposts --values my-outpost.yaml
```

```yaml
# my-outpost.yaml
image:
  digest: sha256:…                 # pin the image you verified
policy:
  control_plane: https://outposts.example.com
  door_keys: ["…"]                 # as the platform gives them
  endpoints:
    tracker:
      url: http://tracker-mcp.tools.svc:8080/mcp
      tools: ["search_*", "get_*"]
      writes: deny
      auth: { secretName: tracker-mcp-token, secretKey: token }
enrolment:
  existingSecret: factory-floor-enrolment
networkPolicy:
  controlPlane:
    cidrs: ["203.0.113.10/32"]     # the door's addresses, when you know them
```

Then read the fingerprint the enrolment printed, and have a person approve the instance in the
platform's console **only if the fingerprint shown there is the same**:

```bash
kubectl -n outposts logs factory-floor-choregos-outpost-0 -c enrol
```

Until it is approved the instance is pending: it polls, and is handed nothing. `helm install`
prints these steps again, with your names in them. A complete example of the values is
[`values/example.yaml`](values/example.yaml).

## What the chart renders

| Object | What it is |
| :-- | :-- |
| StatefulSet `<name>` | the daemon, one instance per replica, each on a claim of its own |
| ConfigMap `<name>-policy` | the policy file, rendered from `policy` |
| ServiceAccount `<name>` | bound to no Role; its token is mounted nowhere |
| NetworkPolicy `<name>` | egress to DNS, the door and the endpoints' servers; no ingress |
| per entry of `servers` | a Deployment, a Service and a NetworkPolicy `<name>-<server>` |

No Secret, no Role, no Service for the daemon: it listens on nothing, which is also why it has no
probe. `<name>` is the release's name, followed by `-choregos-outpost` when it does not hold it
(`fullnameOverride` sets it).

**One identity and one journal per instance.** An instance makes its two key pairs itself and
keeps a journal of call ids, which is what keeps a write from being made twice after a restart
([ADR 0149](../../docs/adr/0149-a-tool-may-live-at-an-outpost.md) §5). Both live on the instance's
volume, with its audit log. The chart is a StatefulSet with a claim template for that reason: each
replica has its own claim, which follows it across restarts, and no second pod ever starts on it.
Losing the volume is losing the identity: the instance is then a new one, to enrol and to approve.
Deleting the journal while keeping the keys removes the protection against a second execution.

**Locked down.** The pod mounts no service account token and no service links, runs as user 1000
and not root, with a read-only root filesystem, every capability dropped and the default seccomp
profile, as a server the platform runs for a plugin is
([ADR 0096](../../docs/adr/0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md) §2).
Two directories are writable, both on the instance's claim: the state (`/var/lib/choregos-outpost`)
and the audit log's (the directory of `policy.audit.file`).

## The policy

`policy` is the policy file, field for field (`control_plane`, `door_keys`, `endpoints`,
`concurrency`, `audit.file`), with two differences that are the chart's:

- **a credential is never a value.** `endpoints.<alias>.auth` names a Secret of the namespace that
  you create (`secretName`, `secretKey`). The chart mounts that key read-only and writes the policy's
  `auth.from_file` with its path. A credential typed in the values fails the render;
- `endpoints.<alias>.network` is not part of the policy file: it tells the NetworkPolicy where the
  server's pods are, when the URL does not say it (below).

What the chart can check is refused when it renders, under the field's name: a missing
`control_plane` or `door_keys`, a key that is not one, an unknown field, a value out of its range.
The daemon checks the whole file again when it starts, and does not start on an invalid one.

The daemon reads its policy at start and never again: a change of `policy` restarts the pods (the
StatefulSet carries the policy's checksum). **Rotating the door's keys** is such a change: add the
next key to `door_keys` when the platform announces it, and remove the former one when the platform
says the rotation is over. Nothing the platform sends changes that list.

## Enrolment

The daemon has no "enrol if needed". The pod's init container looks first: an instance that already
enrolled is left alone, and `choregos-outpost enrol` runs only for an instance without an identity,
with the token of `enrolment.existingSecret`. The token is mounted in that container only.

- Without an identity and without a token, the init container fails and says which Secret to
  create; it starts again by itself and takes the token when it is there.
- A token enrols one instance, once. With `replicas` above 1, each further instance needs a fresh
  token: put it in the same Secret, and the replica that waits takes it at its next try.
- Once an instance is enrolled the token is spent and the Secret can be deleted.

## The network

The daemon calls two kinds of places and nothing else: the door, and the endpoints of its policy.
The NetworkPolicy opens DNS, those, and the servers of `servers`; it has no ingress rule at all.

A NetworkPolicy names pods and addresses, never a host:

- **the door**: `networkPolicy.controlPlane.cidrs` when you give its addresses; otherwise its port
  on every public address, private ranges excluded. Behind an outbound proxy (`proxy.url`, or
  `proxy.existingSecret` when the URL carries a password), the way to the door is the proxy: give
  its addresses, or its namespace when it runs in this cluster;
- **an endpoint**: the pods of the namespace its Service's name carries
  (`<service>.<namespace>.svc`; this namespace for a bare name), on the URL's port. `network`
  narrows it to the pods' labels, gives the pods' port when the Service maps another, or gives
  addresses for a server outside the cluster. An endpoint the policy cannot name fails the render;
- `networkPolicy.extraEgress` is yours to write, and `networkPolicy.enabled: false` renders none.

A private certificate authority, or an inspecting proxy's, goes in `ca.configMap` or `ca.secret`.
Certificate verification is never switched off.

## Servers beside the daemon

`servers` runs MCP servers **you** choose, beside the daemon
([ADR 0149](../../docs/adr/0149-a-tool-may-live-at-an-outpost.md) §9): the platform deploys nothing
at an outpost and sends it no secret. Each entry is an image, a port, and the Secrets its
environment comes from (`envFromSecrets`). It runs locked down as the daemon is, behind a Service
`<name>-<server>` that admits the daemon's pods and nobody else, and reaches nothing unless you open
it (`egress`). Point an endpoint of the policy at it: `http://<name>-<server>:<port>/…`.

## Stopping, restarting, upgrading

- `graceSeconds` (20) is how long the daemon lets calls in progress finish when it is told to stop.
  A write that has begun is left to finish. `terminationGracePeriodSeconds` must be above it.
- `run` ends with a code that says why. Two restart in a loop **on purpose**: 3, the door no longer
  serves this daemon's protocol (upgrade the image); 4, the instance or its outpost was revoked.
  The others: 2 it could not start, 5 the disk refused the journal or the audit log.
- The daemon never upgrades itself and no message of the platform makes it download anything
  ([ADR 0147](../../docs/adr/0147-an-outpost-only-dials-out.md) §9). You choose when: verify the
  image's signature, set `image.digest`, upgrade the release. Pods are replaced one at a time.
- `kubectl exec <pod> -c outpost -- choregos-outpost doctor` checks the policy, the path to the
  door, the clock and each endpoint.

## What this chart does not do

- It does not rotate the audit log, nor ship it anywhere: the file is on the instance's volume.
- It does not make a network plugin enforce the NetworkPolicy: where none does, the daemon's pods
  reach whatever the cluster lets them reach.
- It renders no FQDN-based policy. Where your network plugin has one, it is the way to name the
  door's host exactly.
