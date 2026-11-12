# Running an outpost

For the person who runs an outpost in **their own** network. You may never have seen the platform,
and you are reading this to decide whether to install its daemon. This page says what the daemon
does and what it cannot do, what it asks of you, and what stays yours. Whoever operates the
platform, or administers your organisation on it, reads [Outposts](outposts.md) instead.

An **outpost** is a place of your organisation that the platform (Choregos) cannot connect to, and
that connects out to it. The daemon `choregos-outpost` runs there. It polls one hostname of the
platform, the **door**, for tool calls the platform has already decided; it makes the ones your own
policy file allows, against MCP servers of your network; and it sends the answers back. The
decisions behind it are [ADR 0147](adr/0147-an-outpost-only-dials-out.md),
[ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) and
[ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md), all three still proposed. The daemon's source
is `packages/outpost`, under the Apache 2.0 licence: a program that takes work inside your network is
one you are meant to be able to read.

**Where this stands.** The daemon, the door and the chart are built, and each is tested against the
door's contract. Nothing has run against a real outpost on a real network yet: the end-to-end proof
is a story of its own (S65-19). The [last section](#what-is-not-there-and-what-is-not-proven) says
what that leaves open.

## An outpost does not give data residency

Read this before anything else. An outpost keeps your network's **addresses and credentials** away
from the platform. It does not keep your **data** there.

- A call's arguments come from the platform: an agent wrote them, and the platform decided the call.
- The result your server answers is sealed to the platform and sent to it. There it is bounded and
  screened, handed to a model through the platform's model gateway, and kept where the platform
  keeps what a tool returned (a conversation's history, the recorded result of a governed write).
  Where that model runs, and how long those records live, are the platform's to say, not yours.
- A discovery sends the platform the names, the descriptions and the argument schemas of the tools
  your policy lets through.

So: **a result that must not leave your network must not be a tool's answer.** What you control is
what this page describes: which servers you put behind an alias, which tools each alias lets
through, the account each server is opened with, and a ceiling on what a result may weigh. The
daemon does not read, classify or redact a result: it weighs it, seals it and sends it.

What crosses the door, each way:

| | Leaves your network | Stays in your network |
| :-- | :-- | :-- |
| About your servers | the alias names; the tools each alias serves, when the platform asks for a listing | their addresses, their credentials, whatever your policy does not let through |
| About a call | its result, sealed; or the code of a refusal, which names a class of refusal and never the rule | the audit line you keep of it |
| About the instance | its two public keys, its version, when it last polled; at enrolment its host name and its platform, unless you say `--no-facts` | its two private keys |

What comes in with a call: the alias, the tool's name, the arguments, a deadline, and the names the
platform asserts about who the call is made for (below). No prompt, no model key, no source code and
no run token ever reaches an outpost.

## What the daemon does, and what it cannot do

What it does:

- It holds two key pairs it made on your machine: an Ed25519 key that signs every request it sends,
  and an X25519 key that opens the calls sealed to it. The private halves never leave the machine,
  are never sent and never logged.
- It polls the door, one request at a time. A poll waits 25 seconds at most and the daemon polls
  again at once.
- For each call the door hands over, in this order: it looks in its journal (a call id it has seen
  gets what it got the first time, and its server is not called again); it checks the door's
  signature against the keys your policy file pins, before it opens anything; it opens the call and
  applies your policy file; it asks the alias's **own server** whether the tool writes; it makes the
  call, with the credential read from the file your policy names; it seals the answer to the
  platform and sends it.
- It writes one record of all that in a local audit log that is yours, and a journal of call ids.

What it cannot do. These are properties of the program, read from its source and from what a
running daemon was seen to dial (`packages/outpost/tests/test_only_dials_out.py`):

- **It only dials out.** Every connection is one it opens. **It opens no port**: it has no listening
  socket, which is also why it has no health endpoint and no metrics endpoint.
- **It calls two kinds of places and no third**: the door (`control_plane` in your policy file) and
  the endpoints that file names. No telemetry, no update check. A redirect elsewhere is refused
  before it is sent.
- **No message of the door makes it download or run anything.** It never updates itself. It starts
  no other program and evaluates nothing that arrived as data.
- **It makes only the calls your policy allows and the door signed.** A call no pinned key signed is
  not opened. An alias your file does not hold, a tool outside the alias's patterns, a write where
  `writes` is not `allow`, arguments above the ceiling: each is refused, and your server receives
  nothing.
- **Nothing the door sends changes your policy file**, nor the list of keys it pins.

What it does not decide on: the project, the agent, the run or the person a call is "for". Those are
names the platform asserts. A local rule on them would stop nobody who holds the platform, so the
policy file has no such rule; the daemon writes the names to your audit log, as assertions.

What this does not protect you from: whoever holds the platform, or the door's signing key, can ask
your outpost for **what your policy allows**. They hold no address and no credential of your side,
and cannot add an alias, widen a pattern or turn writes on. Your policy bounds what can be asked of
you. It does not remove it: write the policy as if the platform could be compromised.

## The one firewall rule

**Outbound HTTPS to one hostname**: the door's, which is the host of `control_plane` in your policy
file, on port 443 (or the port that origin names). The organisation's administrator gives you that
origin with the enrolment token. Nothing else is needed towards the platform, and nothing inbound at
all: no rule, no port forward, no VPN. The machine also needs to resolve that name, and to reach the
servers of your own network that the policy names.

What travels on that rule:

- `POST` requests to four paths under `/outposts/v1`: `enrol`, `poll`, `calls/{id}/answer` and
  `calls/{id}/refuse`. The contract is `packages/contracts/outpost-door.yaml`.
- A poll stays open for up to 25 seconds with nothing on it. A firewall or a proxy whose idle timeout
  is shorter cuts every poll: the daemon logs `door.unreachable` and comes back after a wait that
  doubles, up to a minute.
- An answer the daemon sends weighs 2 MiB at most. A poll's answer weighs at most 64 KiB plus 2 MiB
  for each call the daemon said it could take.
- **The clock matters.** A request is refused when the machine's clock is more than 60 seconds from
  the door's (`stale_iat`; the daemon logs `outpost.clock_drift` with the door's time). The daemon
  never corrects its clock from the door's: keep it set (NTP).

### Behind a proxy that inspects TLS

Give the proxy with `--proxy` (or `HTTPS_PROXY`, unless `NO_PROXY` covers the door), and its
certificate authority with `--ca-file`. Certificate verification is never switched off. The proxy
carries the connection to the door only: your endpoints are called directly. The rule to write on
the proxy is a tunnel to the door's hostname, port 443, and no other.

An organisation's outbound proxy often terminates TLS to inspect it. The protocol is built for that:

| The proxy | |
| :-- | :-- |
| **sees** | who talks, when and how much; and what is not sealed: the instance's id, the ids of the calls, the alias names your policy serves, the daemon's version, the code of a refusal, the door's time and protocol window; at enrolment, the token, the instance's public keys and the facts about the machine |
| **cannot read** | a call (its alias, its tool, its arguments, the names it asserts) or its answer: both are sealed end to end, to a key of your instance one way and to a key found inside the call the other way (X25519, HKDF-SHA256, ChaCha20-Poly1305) |
| **cannot forge** | a call: every call is signed by the door, and the daemon makes none that a key of your `door_keys` did not sign. A request of your instance: each is signed, and the signature binds the method, the URL, a digest of the body, the time, a nonce and a sequence number. The keys at enrolment: the enrolment is signed with the key it enrols, and the fingerprint the daemon prints is computed from its own key, never read from an answer |
| **can** | stop, delay or starve the daemon, as it can by dropping packets. Only calls are signed: the rest of what the door answers is not, so whoever sits on the path can also answer that the daemon's protocol is too old, or that the instance was revoked, and the daemon will stop ([exit codes](#exit-codes) 3 and 4). It cannot make the daemon call anything, nor read an answer |

A token read on the path enrols an instance that nobody approves: an instance is handed nothing
until a person has compared its fingerprint ([Enrolment](#enrolment)).

### The first poll's extra round trip

A request carries a nonce the door issued. The daemon's first signed request has none yet: the door
refuses it once (401 `stale_nonce`) with a fresh nonce, and the daemon signs again. One refused
request at every start is therefore expected in a proxy's log, and one more after the platform
rotates the door's key, because the nonces rotate with it.

## The policy file

One YAML file, yours. The daemon reads it when it starts and never again: a change takes a restart.
It does not start on a file that is not valid, and says which field is at fault. The platform cannot
write it. Its schema is `packages/contracts/schemas/outpost-policy.schema.json`, and this complete
example is `packages/contracts/schemas/examples/outpost-policy.example.yaml`:

```yaml
control_plane: https://outposts.example.com
door_keys: ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw"]   # the only keys whose calls it makes
endpoints:
  tracker:
    url: http://tracker-mcp.tools.svc:8080/mcp     # known to the outpost only
    auth: { header: Authorization, from_file: /secrets/tracker-token }
    tools: ["search_*", "get_*", "create_issue"]
    writes: allow                                  # deny by default
    timeout_s: 60
    max_payload_kb: 512
concurrency: 8
audit: { file: /var/log/choregos-outpost/audit.jsonl }
```

Every key:

| Key | Default | What it says |
| :-- | :-- | :-- |
| `control_plane` | required | The door's origin: a scheme and a host, with a port if any, and no path. Every connection the daemon opens towards the platform goes there, and each request's signature binds it. `http` is for a local bench |
| `door_keys` | required | The door's signing keys this outpost obeys: one to four distinct keys ([below](#the-doors-keys-pinning-and-rotation)) |
| `endpoints` | required, may be empty | The MCP servers this outpost may call, 64 at most, each under an **alias**: a lowercase letter, then lowercase letters, digits or `-`, 63 characters at most. The alias is the only thing the platform knows of a server |
| `endpoints.<alias>.url` | required | The server's MCP endpoint (Streamable HTTP), `http` or `https`, without credentials, query or fragment. It exists in this file only |
| `endpoints.<alias>.tools` | required | The tools a call may name, as patterns: `*` stands for any run of characters, nothing else is special, and the whole name must match. An empty list serves none |
| `endpoints.<alias>.writes` | `deny` | `deny` or `allow` ([what a write is](#what-a-write-is)) |
| `endpoints.<alias>.auth` | none | The credential that opens the server. Absent, the server is called without one |
| `endpoints.<alias>.auth.from_file` | required under `auth` | An absolute path the daemon reads the credential from, at each call: a rotated credential is picked up without a restart. Its content never leaves the outpost and is masked in the daemon's log |
| `endpoints.<alias>.auth.header` | `Authorization` | `Authorization` sends `Bearer` and the file's content; another name sends the content as it is |
| `endpoints.<alias>.timeout_s` | `30` | Seconds the daemon waits for the server, 1 to 300; a call also ends at its own deadline, which the platform sets and nothing the daemon sends moves |
| `endpoints.<alias>.max_payload_kb` | `256` | The most a call's arguments, or its result, may weigh, in KiB of JSON, 1 to 1024. Arguments above it are refused; a result above it is not sent, and the platform is told the call was made and its result was too large |
| `concurrency` | `4` | Calls in progress at once, all endpoints together, 1 to 64. One more is refused `busy`, never queued |
| `audit.file` | required | An absolute path: [the local audit log](#the-local-audit-log) |

There is no key that names a project, an agent or a person, on purpose (above).

### The door's keys: pinning and rotation

**What pinning is.** Sealing hides a call; it does not say who sealed it. So every call the door
hands over is signed by the door, and the daemon makes only the calls signed by a key that
`door_keys` lists. A call signed by no key of the list is not opened, your server is not called, and
the call is refused with one code, `signature_refused`, with one line in your audit log that says
nothing of what the call held. A policy file without `door_keys` is not valid, so the daemon does not
start: an outpost that cannot check who orders it does not serve.

**Where the keys come from.** From the platform's management side, never from the door: the
organisation's administrator reads them on the outpost's page of the console, or with the
platform's CLI, where they are printed as the `door_keys` line to paste, beside the `control_plane`
line. Each is the unpadded base64url of an Ed25519 public key's 32 bytes, 43 characters. Nothing the
door sends, at enrolment or later, adds a key to your file or changes one: there is no trust on
first use, because an enrolment's answer crosses the same path as a call.

**How a rotation goes.** It is an overlap, and you take part in it:

1. The platform announces the next key: the administrator now reads two keys where there was one,
   and passes them to you.
2. You add the next key to `door_keys` and restart the daemon. Both keys are pinned.
3. Only then does the door sign with the next key.
4. When the platform says the rotation is over, you remove the former key and restart.

If the door signs with a key you did not add, every call is refused `signature_refused`: nothing is
executed, the platform sees the refusals, and your audit log holds one line per call. No message of
the protocol rotates a pinned key for you. `doctor` checks that each entry is a key; it cannot check
that it is the door's.

### What a write is

`writes` is `deny` unless the file says `allow`. Under `deny`, the daemon refuses (`write_denied`)
any tool that the endpoint's own server does not declare read-only.

"A write" here means exactly that: **what your own server declares**. Before each call the daemon
asks the alias's server for its tool listing and reads the tool's `readOnlyHint`. A tool the server
declares read-only is a read. A tool it does not declare read-only is a write, and so is a tool it
does not list. Whether a tool writes is never read from the call: a platform that said "this is only
a read" would be believed by nobody here. A tool listing shows the platform only the read-only tools
of an alias whose `writes` is `deny`.

It follows that `writes: deny` is as good as your server's declarations: a server that marks a tool
read-only while it writes is believed. Behind `deny`, prefer an account that cannot write.

In this version the platform opens no write through an outpost at all
([below](#what-is-not-there-and-what-is-not-proven)): it hands over only calls to tools it recorded
as reads when it discovered them. `writes` still matters, because the daemon reads the declaration
again at each call: if your server no longer declares such a tool read-only, `deny` refuses the call
and `allow` makes it. `allow` also makes a listing show the tools that write.

### What the daemon refuses

Deny by default. Each refusal is one code of a closed list, one line of your audit log, and your
server receives no call:

| Code | When |
| :-- | :-- |
| `signature_refused` | no pinned key signed the call; it was not opened |
| `call_unreadable` | the call does not open with this instance's key, or is not a call |
| `unsupported_kind` | a kind of call this daemon does not know (a later protocol's) |
| `deadline_passed` | the call's deadline is behind, by this machine's clock |
| `unknown_endpoint` | the file holds no such alias |
| `tool_not_allowed` | the tool is outside the alias's patterns |
| `payload_too_large` | the arguments weigh more than `max_payload_kb` |
| `busy` | `concurrency` is reached, or the daemon is stopping, or it was restarted before it began the call |
| `write_denied` | the server does not declare the tool read-only, and `writes` is not `allow` |
| `endpoint_unavailable` | the daemon could not reach or open its own server, and sent it nothing |
| `cannot_record` | the disk refused the journal or the audit log ([below](#when-the-disk-refuses-a-record)) |
| `cancelled` | the platform gave the call up before the daemon started it |

The platform reads the code, which names a class of refusal and never the rule that produced it.

## Enrolment

An instance is one copy of the daemon, with its own keys. Each instance enrols once, with a token
of its own, and a person approves it.

1. The organisation's administrator creates the outpost on the platform and asks it for an
   **enrolment token**. They pass you three things: the door's origin (`control_plane`), the door's
   keys (`door_keys`) and the token. **The token enrols one instance, once, and lives fifteen
   minutes at most.**
2. Write the policy file first: `enrol` reads the door's origin from it, and does not run on an
   invalid file.
3. Enrol:

   ```bash
   choregos-outpost enrol --token-file /run/secrets/enrolment-token
   ```

   The token is read from `--token-file`, from `--token`, from `CHOREGOS_OUTPOST_ENROLMENT_TOKEN`,
   or asked for at a terminal without being echoed. The daemon makes its two key pairs if it has
   none, presents the token with the public halves, and prints the **fingerprint** of its signing
   key:

   ```text
   Enrolled as instance b3c1f6a2-6d0e-4b7a-9a55-2f1c8e7d4a90 of outpost acme/factory-floor: pending.
   The keys were made on this machine.

   Fingerprint of this instance's signing key:

       SHA256:09wT63TkuDIeTj6MxD5TFt7dxaJNpfKNrUkVyJymRJE

   Approve the instance in the console only if the fingerprint shown there is this one.
   Then start the daemon: choregos-outpost run
   ```

4. **A person approves the instance in the platform's console, by comparing the fingerprint shown
   there with the one printed here.** Read it to them yourself. Until then the instance is
   *pending*: it may poll, and is handed nothing. If the daemon warns that the answer echoed another
   fingerprint, something on the path rewrote the enrolment: do not have it approved.

What else to know:

- With the enrolment the daemon sends two facts for the person who approves, the machine's host name
  and its platform (`linux/x86_64`). `--no-facts` sends none.
- `enrol` ends with code 0 when the instance is enrolled, 1 when the door refused or did not answer,
  2 when it could not start (the policy file, the keys, no token). A refused enrolment does not use
  the token up.
- If the answer was lost on the way, ask for a new token and enrol again: while the instance is
  still pending at the same outpost, it keeps its keys and is the same instance. A key that was
  approved or revoked, or that is pending at another outpost, is refused.
- You may start `run` before the approval. The daemon logs `outpost.pending` once, with the
  fingerprint, asks again every 30 seconds at most, and is served as soon as it is approved, without
  a restart.
- A second instance of the same outpost (availability, a rolling upgrade) has its own state
  directory, takes its own token and is approved on its own. **Never copy an identity to a second
  machine**: two daemons under one key is what a stolen key looks like, and the door treats it so
  (it refuses the second poll and raises an alert at the platform).
- A revoked instance does not come back: its key is refused for as long as its outpost exists. A
  replacement is a new instance: an empty state directory, a new token, a new approval.

## Running the daemon

Three commands. The program's own options come before the command, a command's options after it,
and most can come from the environment instead:

```bash
choregos-outpost enrol     # make the keys, present an enrolment token, print the fingerprint to compare
choregos-outpost run       # poll the door and make the calls the policy file allows, until told to stop
choregos-outpost doctor    # check the policy, the path to the door, the clock and each endpoint
```

| Option | Environment | Default | What |
| :-- | :-- | :-- | :-- |
| `--policy` | `CHOREGOS_OUTPOST_POLICY` | `/etc/choregos-outpost/policy.yaml` | the policy file |
| `--state-dir` | `CHOREGOS_OUTPOST_STATE_DIR` | `/var/lib/choregos-outpost` | the identity and the journal; it must outlive a restart |
| `--identity-dir` | `CHOREGOS_OUTPOST_IDENTITY_DIR` | `identity` under the state directory | the keys, when they live elsewhere |
| `--ca-file` | `CHOREGOS_OUTPOST_CA_FILE` | none | an authority to trust besides the system's: an inspecting proxy's, a private one |
| `--proxy` | `CHOREGOS_OUTPOST_PROXY` | `HTTPS_PROXY`, unless `NO_PROXY` covers the door | the outbound proxy towards the door only |
| `--log-level` | `CHOREGOS_OUTPOST_LOG_LEVEL` | `INFO` | the daemon's own log |
| `--version` | | | print the daemon's version and the protocol it speaks |
| `enrol --token` | `CHOREGOS_OUTPOST_ENROLMENT_TOKEN` | | the enrolment token |
| `enrol --token-file` | | | a file that holds the token |
| `enrol --no-facts` | | | send no fact about this machine |
| `run --grace` | `CHOREGOS_OUTPOST_GRACE_S` | `20` | seconds the calls in progress have to finish when the daemon is told to stop |
| `doctor --json` | | | print the checks as JSON |

**What it keeps on disk**, and what you must keep for it:

| Where | What | If it is lost |
| :-- | :-- | :-- |
| the identity directory | the two private keys (mode 0600) and what the door answered at enrolment | the instance is gone: enrol a new one and have it approved |
| `journal` under the state directory | one line per step of a call (ids, names, digests, never arguments or results), a call's sealed answer until its deadline, the last sequence number signed, a lock | **the protection against a second execution of a write is gone.** Do not delete the journal while you keep the keys |
| `audit.file` | the audit log (mode 0640) | your own record of what was asked |

A key file that others can read, or that its group can write, is refused at start. One daemon holds
a journal at a time: a second one on the same directory does not start. The package's own page,
`packages/outpost/README.md`, says how the journal is bounded.

**Its own log** is JSON lines on standard output, apart from the audit log, and no line carries a
credential or a key. The daemon exposes no metrics and no health endpoint, since it listens on
nothing; and after `outpost.started` a healthy daemon with nothing to do logs nothing. Whether the
platform sees your instance is shown at the platform, on the outpost's page (*Online*, or when it
was last seen). Lines worth an alert on your side: `door.unreachable` that lasts, `door.refused`,
`outpost.clock_drift`, `door.poll_conflict`, `endpoint.unavailable`, `call.left_unanswered`,
`outpost.cannot_record`, `outpost.protocol_unsupported`, `outpost.revoked`.

**Paused.** When the organisation pauses the outpost, the daemon is told at its next poll
(`outpost.paused`), keeps polling and is handed nothing until it is resumed. It does not exit.

**Cancelled.** When the platform gives a call up, a call the daemon has not started is refused
(`cancelled`) and a read in flight is abandoned. **A write that has begun is left to finish**:
stopping the request would not undo it, and your audit log should say what it did.

**Stopping.** `SIGTERM` and `SIGINT` end the polling; what the daemon holds has the grace period
(`--grace`, 20 seconds) to finish and answer. A call still running after it is cut
(`outpost.left_running`) and stays as the journal has it: the next start reports it to the platform,
as unanswered if it had begun, and does not execute it again. The grace period is shorter than an
endpoint's default `timeout_s`: give it more than the longest of your timeouts if no call is to be
cut.

### `doctor`

```bash
choregos-outpost doctor
choregos-outpost doctor --json
```

One line per check, `ok`, `skipped` or `FAILED` with what to do; it ends with code 1 when one fails.
It signs nothing and polls nothing: it asks the door a request the door must refuse, so it can run
beside a running daemon without taking its place. It does call each of your endpoints, for a tool
listing.

| Check | What it means when it is `ok` | When it fails |
| :-- | :-- | :-- |
| `policy` | the file is valid, and each entry of `door_keys` parses as a key | the daemon does not start on this file; the faults are named by their field. Every check below it except `identity` is skipped |
| `identity` | the keys are there and are the daemon's alone; it shows the fingerprint and the instance | a key is missing, or open to others. Skipped before the first `enrol` |
| `records` | the audit log and the journal can be written | the daemon executes nothing it cannot record: free the disk, fix the rights |
| `dns` | the name the daemon dials resolves: the proxy's when there is one, the door's otherwise | check the resolver this machine uses, and the name |
| `proxy` | the outbound proxy opens a tunnel to the door. Skipped without a proxy, and for a proxy whose URL is not `http://` | the proxy must let this machine open a tunnel to the door's hostname, port 443; a 407 means it wants credentials, in its URL |
| `tls` | the certificate chain presented for the door verifies | it names who issued what was presented: an inspecting proxy's or a private authority goes in `--ca-file` |
| `door` | what answers at `control_plane` is the door | the origin cannot be reached; or outposts are switched off at the platform, or `control_plane` is not the door's hostname |
| `clock` | the machine's clock is within 60 seconds of the door's | every request would be refused: set the clock |
| `endpoint <alias>` | the alias's server answers a tool listing with its credential; it shows how many tools are listed, how many fall within the patterns and how many of those are declared read-only | check the alias's `url` and its `auth` |

What `doctor` does not tell you: whether the instance is approved, whether the keys you pinned are
the ones the door signs with, and whether anything enforces the limits of your network.

### Exit codes

`run` ends with a code that says why:

| Code | Meaning | What to do |
| :-- | :-- | :-- |
| 0 | it was told to stop | nothing |
| 2 | it could not start: the policy file, the identity (no key, a key open to others, keys that never enrolled) or the authority file | read the message, run `doctor` |
| 3 | the door no longer serves this daemon's protocol | upgrade the daemon ([below](#upgrades-are-pulled-and-verified)) |
| 4 | the instance, or its outpost, was revoked | stop it; ask the organisation's administrator why |
| 5 | the disk refused the journal or the audit log, at start or while it ran | see [below](#when-the-disk-refuses-a-record) |

**Codes 3 and 4 end the process on purpose, and a supervisor will restart it in a loop**: a
container restart policy, a Kubernetes pod, a service unit. Each restart polls once, is told the same
thing and exits again. That loop is the signal, not a fault of the supervisor, and restarting will
never mend it:

- **3**: nobody upgrades the daemon for you, and the door hands a daemon below its minimum protocol
  nothing. Upgrade the image, then the loop ends by itself.
- **4**: a revoked key does not come back. Stop the supervisor (with the chart, uninstall the
  release or set `replicas` to 0), and find out from the administrator whether the instance was
  retired or its key is thought stolen. If the outpost is to serve again, enrol a new instance.

Both can also be the work of whoever sits on the path, since those two answers of the door are not
signed ([above](#behind-a-proxy-that-inspects-tls)): if the administrator says nothing was revoked
and the platform was not upgraded, look at the path.

### When the disk refuses a record

The daemon executes nothing it cannot record. A write's id reaches the disk before the write is
executed, and an audit line is on disk before the daemon acts on it. When the disk refuses either
(full, read-only, wrong rights):

1. the call the record was for is **refused** (`cannot_record`), and your server receives nothing;
2. an audit line that could not be written is put on the daemon's own log instead
   (`audit.line_lost`, digests only);
3. the daemon **stops**: it polls no more, lets the calls in progress finish and answer, and exits
   with code 5.

It does not start either while the journal or the audit log cannot be opened. Free the disk or mend
the rights, and start it again. Do not get it going by deleting the journal: that file is what keeps
a write from being made twice.

## The local audit log

The file `audit.file` names is **your own record**, written by the daemon on your disk, independent
of the platform's. Nothing sends it anywhere.

- **One line when an assignment is received, and one when it is settled**: `executed`, `refused`, or
  `replayed` when a call id the journal already held got what the journal held. A call refused for
  its signature has a single line.
- **A line holds**: when (`at`), the `event`, the `call_id`, the pinned key that signed the call
  (`signed_by`), the `kind` of call, the alias (`endpoint`), the `tool`, a digest of the arguments
  (`arguments_sha256`), whether your server declares the tool a write (`write`), the `outcome`
  (`result`, `error`, `unanswered`, `oversized`) or the `refusal` code, how long the server took
  (`duration_ms`), and the names the platform `asserted`: the organisation, the project, the run,
  the work item, the agent, who started it, the action, its approvers, the connector.
- **It holds digests, never arguments or results.** The line of a call refused for its signature
  holds nothing of what the call carried: it was not opened.
- **The asserted names are assertions.** The daemon logs them as the platform wrote them and decides
  nothing on them.

```json
{"at":"2026-11-12T10:03:44.009Z","event":"refused","call_id":"c41d8a70-2f5e-4b19-a3c6-0e9b7d5f1a62","signed_by":"3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw","kind":"call_tool","endpoint":"tracker","tool":"delete_project","arguments_sha256":"09e7ba39a93ec48fdfcf3bb16b29d483e898075eff8471fcc5b015c70b28c209","refusal":"tool_not_allowed","asserted":{"sub":"run:r-20261112-0043","org":"acme"}}
```

The schema of a line is `packages/contracts/schemas/outpost-audit-line.schema.json`, and a short log
is `packages/contracts/schemas/examples/outpost-audit-log.example.jsonl`.

**It can be compared with the platform's record.** The platform keeps, for each call it routed to
your outpost, the same call id and the same digest of the arguments (the SHA-256 of their canonical
JSON), with the state of the call and the code of a refusal; the organisation's administrator reads
them on the outpost's page or with the platform's CLI. A call in your log that the platform does not
know, or the reverse, is a question to ask. The line holds no digest of the result: what your log
proves is what was asked and what became of it, not what was returned.

**Keeping it is yours.** The daemon does not rotate the file and does not ship it. It opens it for
appending at each line and flushes each line to the disk, so a rotation by renaming loses nothing.
How long you keep it is your decision. Mind where it lives: when its disk is full the daemon stops
([above](#when-the-disk-refuses-a-record)).

## Installing with the chart `choregos-outpost`

The daemon ships as a container image, `ghcr.io/vargafoundation/choregos-outpost`, and as a Helm
chart for your own cluster, `charts/choregos-outpost`, which each release publishes beside the
platform's, at the same version. The chart is not a subchart of the platform's and shares no value
with it. Its page, `charts/choregos-outpost/README.md`, holds every value, and
`charts/choregos-outpost/values/example.yaml` is a complete example.

```bash
kubectl create namespace outposts
kubectl -n outposts create secret generic factory-floor-enrolment --from-literal=token=<token>
helm install factory-floor oci://ghcr.io/vargafoundation/charts/choregos-outpost --namespace outposts --values my-outpost.yaml
kubectl -n outposts logs factory-floor-choregos-outpost-0 -c enrol
```

The last command prints the fingerprint to have approved. What to know before you install:

- **A volume per instance.** The chart renders a StatefulSet with a claim per replica: an instance's
  keys, its journal and its audit log live on its own claim, which follows it across restarts, and
  no second pod ever starts on it. Losing the claim is losing the identity. Each replica enrols with
  a token of its own and is approved on its own.
- **The policy is the chart's `policy` value**, field for field, rendered into a ConfigMap; a change
  restarts the pods. A credential is never a value: `auth` names a Secret you create, which the
  chart mounts as the file `auth.from_file` points to.
- **Enrolment is an init container**, which enrols an instance only when it has no identity yet, with
  the token of a Secret you create. The token is mounted in that container only.
- **Locked down**: no service account token, not root, a read-only root filesystem, every capability
  dropped. Two directories are writable, both on the claim.
- **It draws no key and reads nothing back from the cluster**: the instance's keys are made by the
  daemon on its volume, not by the chart. It renders the same under `helm install`, under
  `helm template` piped to an apply, and under a GitOps tool. (The platform's chart is another
  matter for the door's signing key, which is the platform operator's concern:
  [Deployment](deployment.md#outposts-adrs-0147-0149).)
- **MCP servers beside the daemon** are yours to list (`servers`): the platform deploys nothing at an
  outpost and sends it no secret.
- **Check it** once it runs:

  ```bash
  kubectl -n outposts exec factory-floor-choregos-outpost-0 -c outpost -- choregos-outpost doctor
  ```

Outside Kubernetes, run the image under your own supervisor. Its entrypoint is the daemon and its
default command is `run`; it runs as user 1000, reads its policy at
`/etc/choregos-outpost/policy.yaml` and keeps its state under `/var/lib/choregos-outpost`
(`docker/outpost.Dockerfile`). Mount the policy file, a volume that outlives the container for the
state, and the audit log's directory. The repository ships no service unit and no compose file for
it.

### The NetworkPolicy, and its limit

The chart renders a NetworkPolicy for the daemon's pods: **no ingress rule at all**, and egress to
DNS, to the door, to the servers the endpoints name and to the servers of `servers`. Three limits:

- **A NetworkPolicy names pods and addresses, never a host.** Without the door's addresses
  (`networkPolicy.controlPlane.cidrs`), the door's port is opened to **every public address**,
  private ranges excluded: the pod could then reach any public host on that port. Give the door's
  addresses to narrow it, or the proxy's when the way to the door is your proxy. Where your network
  plugin has policies by host name, they are the way to name the door exactly; the chart renders
  none.
- **It holds only where the cluster's network plugin enforces policies.** Where none does, it is
  ignored without a word, and the pods reach whatever the cluster lets them reach.
- **It is not your firewall.** The rule of the [first section](#the-one-firewall-rule) is still to
  write where your network's edge is.

## Upgrades are pulled and verified

The daemon never upgrades itself, and no message of the platform makes it download or run anything.
**You choose when.**

1. **Verify the image's signature.** Release images are signed with cosign, keyless, by the
   repository's release workflow; an SBOM and a provenance attestation are attached
   ([Security](security.md#supply-chain)):

   ```bash
   cosign verify ghcr.io/vargafoundation/choregos-outpost:<version> --certificate-identity https://github.com/VargaFoundation/choregos/.github/workflows/release.yml@refs/tags/v<version> --certificate-oidc-issuer https://token.actions.githubusercontent.com
   ```

   Its output names the digest that was signed (`docker-manifest-digest`).
2. **Pin the image by that digest**, not by a tag: with the chart, `image.digest`. The tag is then
   not read.
3. **Upgrade**: the chart replaces the pods one at a time, each on its own volume. A daemon told to
   stop gives the calls in progress its grace period to finish.

**The protocol window.** The door announces the protocol versions it serves, a current one and a
minimum, in the answer to every poll. A daemon below the minimum is handed nothing: it stops
polling, says so (`outpost.protocol_unsupported`, with the protocol it speaks and the door's
window) and exits with code 3 ([above](#exit-codes)). An enrolment by such a daemon is refused the
same way, and `enrol` prints the door's refusal. The window is part of a platform release, so the
minimum moves only when the platform is upgraded.

What the protocol does not do: warn a daemon ahead of time. A daemon learns that the minimum rose
when it is refused. Being told before the platform raises it is something to arrange with whoever
operates the platform.

## Who is responsible for what

Three parties. The platform's operator runs the platform. The organisation's administrator
administers your organisation on it. You run the outpost.

| | The platform's operator | The organisation's administrator | The outpost's operator |
| :-- | :-- | :-- | :-- |
| **The door** | its hostname, its DNS record and certificate, its rate limit and source restrictions; the switch that turns every outpost off | | the one outbound rule to that hostname; the proxy and its authority |
| **The door's signing key** | keeps it secret and backed up; announces the next key before the door signs with it | reads the keys to pin and passes them on; says when a rotation starts and when it is over | pins them in `door_keys`; adds the next key in time, removes the former one after |
| **The outpost and its instances** | | creates the outpost; issues each token; approves each instance against its fingerprint; pauses, revokes | runs the daemon; reads the fingerprint to whoever approves; keeps each instance's state; never copies an identity |
| **What may be asked** | the door's quotas | declares the connectors (an outpost and an alias), opens each operation, decides which agents call them | the policy file: the aliases, the tool patterns, `writes`, the ceilings |
| **The servers and their credentials** | holds no address and no credential of them | agrees the aliases with the operator | deploys the servers, holds their credentials, chooses the accounts behind them |
| **What a tool returns** | where results go and how long they are kept: the model gateway, the platform's records | which projects and agents read them | which servers and tools are exposed at all |
| **Upgrades** | the platform's, and with them the protocol window the door serves | | the daemon's: verifies the image, pins its digest, chooses when |
| **Records** | the platform's audit log, cost ledger and network journal | reads them, and the outpost's calls | the local audit log (rotation, retention, shipping) and the daemon's own log |
| **The clock** | the door's | | the machine's, within 60 seconds of the door's |
| **A key that may be stolen** | wires the alert on a second poll under one key | reads the event, revokes the instance | says so at once; treats the machine accordingly |

This table is what the mechanisms imply. It is not an agreement between the parties: a
shared-responsibility statement is theirs to write, outside this repository.

## What is not there, and what is not proven

Not there in this version:

- **Writes.** The platform opens a write through an outpost only under the rule that it is delivered
  at most once, and it does not carry that rule yet: it opens no such write. The daemon's half is
  there (the journal, `writes`), and has nothing to apply it to.
- **Runs.** A whole run executing at an outpost is not decided
  ([ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md) §12). The daemon announces one capability,
  `tools`.
- **A person's token.** A call reaches your server under the credential the policy names for the
  alias, and under no other identity.
- **A generic relay.** The platform names an alias and a tool, never a host: an internal API is
  reached as a tool of an MCP server you run.
- **Metrics, a health endpoint, a rotation of the audit log, a notice before the protocol minimum
  rises.** Each is said where it matters above.

Not proven:

- **The daemon and the door together.** Each is tested against the door's contract and against a
  scripted stand-in for the other. A tool call from the platform to a server behind a real daemon,
  end to end, is the story S65-19.
- **Anything on a real network.** No firewall, no outbound proxy that inspects TLS and no idle
  timeout of a real appliance has been in the path. `doctor` and the daemon are tested against
  stand-ins for them.
- **The chart on a cluster.** It is rendered and checked as a document: that a network plugin
  enforces its NetworkPolicy, and that a volume behaves as the daemon needs, is not shown.
- **A rotation of the door's key** with real outposts.

Until S65-19 has run, read this page as what the code is written and tested to do, not as what has
been operated.
