# choregos-outpost

The daemon an outpost's operator installs in their own network
([ADR 0147](../../docs/adr/0147-an-outpost-only-dials-out.md),
[ADR 0148](../../docs/adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md),
[ADR 0149](../../docs/adr/0149-a-tool-may-live-at-an-outpost.md)).

An outpost is a place a person enrols, of one organisation, that only dials out. This program holds
a key pair it generated, polls the platform's door for the calls routed to it, makes them against
MCP servers of its own network under a policy file the platform cannot write, and answers. It is
built against the door's contract (`packages/contracts/outpost-door.yaml`, protocol 1) and nothing
else of the platform: it imports no code of the API.

This page is the package's own. The guide for whoever runs an outpost is a separate document.

## What it does not do

- It opens no listening socket. Every connection is one it opens.
- It calls two kinds of places: the door (`control_plane` in the policy file) and the endpoints the
  policy file names. Nothing else: no telemetry, no update check.
- It never updates itself, and no message of the door makes it download or run anything. A door
  whose minimum protocol is above the daemon's gets one answer: the daemon stops polling and says so.
- It does not give data residency. A tool's result is sealed to the platform and sent there.

## Commands

```
choregos-outpost enrol    # make the keys, present an enrolment token, print the fingerprint to compare
choregos-outpost run      # poll the door and make the calls the policy file allows
choregos-outpost doctor   # check the policy, the path to the door, the clock and each endpoint
```

Options come before the command, or from the environment:

| Option | Environment | Default | What |
|:--|:--|:--|:--|
| `--policy` | `CHOREGOS_OUTPOST_POLICY` | `/etc/choregos-outpost/policy.yaml` | the policy file |
| `--state-dir` | `CHOREGOS_OUTPOST_STATE_DIR` | `/var/lib/choregos-outpost` | the identity and the journal; it must outlive a restart |
| `--identity-dir` | `CHOREGOS_OUTPOST_IDENTITY_DIR` | `<state dir>/identity` | the keys, when they live elsewhere (a volume mounted read-only) |
| `--ca-file` | `CHOREGOS_OUTPOST_CA_FILE` | none | an authority to trust besides the system's: an inspecting proxy's, a private one |
| `--proxy` | `CHOREGOS_OUTPOST_PROXY` | `HTTPS_PROXY`, unless `NO_PROXY` covers the door | the outbound proxy towards the door only |
| `--log-level` | `CHOREGOS_OUTPOST_LOG_LEVEL` | `INFO` | the daemon's own log (JSON lines on standard output) |

`enrol` takes its token from `--token`, `--token-file` or `CHOREGOS_OUTPOST_ENROLMENT_TOKEN`. It
prints the fingerprint of the instance's own signing key, never the one the door's answer echoes:
that answer is not signed. A person approves the instance in the console only if the fingerprint
shown there is the one printed. `--no-facts` sends no fact about the machine (by default its host
name and its platform, for the person who approves).

Certificate verification is never switched off. Behind a proxy that inspects TLS, give its
authority with `--ca-file`.

## The policy file

`outpost-policy.schema.json` in `packages/contracts/schemas` describes it, and
`examples/outpost-policy.example.yaml` there is a complete one. The daemon does not start on a file
that is not valid, `door_keys` included: an outpost that cannot check who orders it does not serve.
The file is read at start; a change takes a restart.

Deny by default. Each refusal is one code of the contract's closed list and one line of the local
audit log, and the endpoint's server receives no call:

| Refused | Code |
|:--|:--|
| a call no pinned key signed (the envelope is not opened) | `signature_refused` |
| an alias the file does not hold | `unknown_endpoint` |
| a tool outside the endpoint's patterns | `tool_not_allowed` |
| a tool the endpoint's own server does not declare read-only, where `writes` is not `allow` | `write_denied` |
| arguments above `max_payload_kb` | `payload_too_large` |
| more calls at once than `concurrency` | `busy` |

Whether a tool writes is read from the outpost's own server (its `readOnlyHint`), at each call,
never from the call. A tool the server does not list is a write.

## Where things live

| Path | Mode | What |
|:--|:--|:--|
| `<identity dir>/signing.key` | 0600 | the Ed25519 private key, PEM (PKCS #8) |
| `<identity dir>/sealing.key` | 0600 | the X25519 private key, PEM (PKCS #8) |
| `<identity dir>/instance.json` | 0600 | what the door answered at enrolment; no secret |
| `<state dir>/journal/journal.jsonl` | 0600 | one line per step of a call: ids, names, digests |
| `<state dir>/journal/answers/<call id>` | 0600 | a call's answer, sealed to the platform, until the call's deadline |
| `<state dir>/journal/sequence` | 0600 | the last request sequence number signed |
| `<state dir>/journal/lock` | 0600 | held while the daemon runs: one instance, one journal |
| `audit.file` of the policy | 0640 | the audit log, one JSON line per event |

The directories are created 0700 (the audit log's 0750). The private keys never leave the machine,
are never sent and never logged. A key file that others can read, or that the group can write, is
refused at start; a key the owner's group can read is accepted, which is what a volume mounted for
a container's group looks like.

## What is recorded, and what happens when it cannot be

One decision per call id. The journal is consulted first: an id it holds gets what it holds (the
same refusal, the answer it kept), and its server is not called again. A write's id reaches the
disk (`fsync`) before the write is executed, and its answer after. A daemon killed in the middle of
a call and restarted reports that call `unanswered` and does not execute it again; a call it had
taken and not begun is refused, since its server received nothing.

**When the disk refuses a record, nothing is executed on top of it.** The call the record was for
is refused `cannot_record`; an audit line that could not be written is emitted on the daemon's own log
(`audit.line_lost`, digests only); the daemon stops polling, lets the calls in progress finish and
answer, and exits with code 5. It does not start either when the journal or the audit log cannot
be opened.

**How the journal is bounded.** An entry stands until its call's deadline (300 seconds after it was
received when its envelope was never opened), and is dropped a minute later; the file is rewritten
at start and every 2000 lines. A sealed answer is deleted at its call's deadline. The exception is
a write that was begun: its id is kept for as long as the journal lives (about 300 bytes each), so
that no clock set back can have it executed twice. Deleting the journal while keeping the keys
removes that protection.

The audit log is not rotated by the daemon. It is opened for appending at each line, so a rotation
by renaming loses nothing.

## Concurrency, cancellation, stopping

One poll at a time. A poll announces how many more calls the daemon can take
(`concurrency` minus the calls in progress); with no room left it asks without waiting and comes
back when a call has ended. A call beyond the ceiling is refused `busy`, never queued.

A call the door cancels before the daemon started it is refused `cancelled`. A read in flight is
abandoned. A write that has begun is left to finish, and its outcome is recorded: stopping the
request would not undo it.

`SIGTERM` and `SIGINT` end the polling; what the daemon holds has `--grace` seconds (20) to finish.
A call still running after that stays as the journal has it, and the next start reports it.

An answer or a refusal the door did not take is sent again, with a wait that doubles, until the
call's deadline.

| Exit code of `run` | Meaning |
|:--|:--|
| 0 | stopped on request |
| 2 | it could not start: the policy, the identity or the files |
| 3 | the door no longer serves this daemon's protocol: upgrade it |
| 4 | the instance, or its outpost, was revoked |
| 5 | the disk refused the journal or the audit log |

## doctor

One line per check, `ok`, `skipped` or `FAILED` with what to do; exit code 1 when one fails,
`--json` for a machine.

| Check | Fails when |
|:--|:--|
| `policy` | the file is not valid, an entry of `door_keys` that does not parse as a key included |
| `identity` | a key is missing, or open to others (skipped before the first `enrol`) |
| `records` | the audit log or the journal cannot be written |
| `dns` | the name the daemon dials does not resolve (the proxy's, when there is one) |
| `proxy` | the outbound proxy is not there, or refuses a tunnel to the door |
| `tls` | the certificate chain presented for the door does not verify; it names who issued it |
| `door` | what answers at `control_plane` is not the door |
| `clock` | this machine's clock is more than 60 seconds from the door's `server_time` |
| `endpoint <alias>` | the alias's server does not answer a tool listing with its credential |

`doctor` signs nothing and polls nothing: it asks the door a request the door refuses. It can run
beside a daemon without taking its place.

## Tests and the fake door

`choregos_outpost.fakes.FakeDoor` is a scriptable door built from the contract: the four verbs, the
order of the checks, the problems, calls sealed and signed as the contract says, under a key
derived from a label. It is reached without a socket (`transport()`) or served by any ASGI server.

```
uv run pytest packages/outpost/tests
```
