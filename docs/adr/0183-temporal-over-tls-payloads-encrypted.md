# 0183 — Temporal over TLS, payloads encrypted: one connection factory, one key per deployment in phase 1

- **Status**: accepted, 2026-11-17; phase 1 implemented by S80-06; phase 2 (a key per organisation, from
  a KMS) not started
- **Concerns**: `choregos_core.temporal_connection` (`connect_temporal`, `TemporalSecurity`,
  `AesGcmCodec`, `data_converter`), every Temporal client and worker (`choregos_orchestrator.worker`,
  `choregos_orchestrator.train_client`, `choregos_api.temporal.RealTemporal`, `tools/replay_record.py`),
  the chart (`global.temporal.tls`, `global.temporal.codec`, `templates/_helpers.tpl`)

## Context

Temporal stores every workflow's history: the inputs and results of every activity, every signal, every
failure message. For Choregos that is the ticket's text, the agent's result, the person's decision, and
on an HR workflow a salary or a medical leave. Until S80-06 every process connected with
`Client.connect(address, namespace=...)`: no TLS, and the SDK's default converter, which writes JSON in
clear. Whoever could read Temporal's database, its backups, or the wire between a pod and the frontend
read the tickets. Five places built a client, each on its own (`worker.py`, three in `train_client.py`,
`RealTemporal`), plus the history recorder in `tools/`.

## Decision

1. **One factory.** `choregos_core.temporal_connection.connect_temporal(address, namespace)` is the only
   caller of `Client.connect` in the shipped code (`tests/architecture/test_one_temporal_connection.py`
   refuses another). It reads the deployment's configuration from the environment and gives the client
   its TLS settings and its data converter; a worker built on that client inherits the converter. The
   API and every worker queue therefore encode and decode alike: a payload one writes, the other reads.
2. **TLS** (`CHOREGOS_TEMPORAL_TLS`, `..._TLS_CA_FILE`, `..._TLS_CERT_FILE`, `..._TLS_KEY_FILE`,
   `..._TLS_SERVER_NAME`). Naming a CA or a client certificate turns TLS on; the switch alone turns it
   on against the system's roots. A certificate without its key, or a file that cannot be read, stops the
   process at its first connection with the variable's name.
3. **The payload codec.** AES-256-GCM over each whole serialized payload, a random 12-byte nonce per
   payload, the key id in the payload's metadata (`encryption-key-id`) and as the associated data, so a
   payload cannot be relabelled under another key. Keys are `id:base64` entries, comma-separated
   (`CHOREGOS_TEMPORAL_CODEC_KEYS`, or a mounted file named by `..._CODEC_KEYS_FILE`): the first
   encrypts, every one decrypts. Rotation: put the new key first, keep the old one until no open history
   carries it; a payload under a key no longer listed fails with that key's name. With the codec on,
   failures carry their message and stack trace encoded too (`DefaultFailureConverterWithEncodedAttributes`):
   a failed activity can quote a ticket as easily as a result can.
4. **Off by default, and a clear payload still reads.** Without configuration the converter is the SDK's
   default, byte for byte what it was, so the archived histories replay unchanged and dev, kind and
   compose need nothing. The codec decodes a payload that is not encrypted as it is: switching it on in a
   running deployment leaves the histories already begun readable.
5. **The chart** mounts a TLS Secret (`ca.crt`, and `tls.crt`/`tls.key` for mutual TLS: a cert-manager
   Certificate's keys) and a codec Secret into the API and every worker queue, and sets the variables.
   A switch without its Secret, or an `extraEnv` entry that would set the same variable, is refused at
   render time.

## What phase 1 does not cover

- **One key for every organisation.** A deployment's key decrypts every organisation's payloads. That
  protects the histories from whoever reads Temporal's storage or backups, not one organisation from an
  operator of the deployment, and it cannot be handed to one organisation for its own data.
- **Workflow ids, search attributes, workflow and activity type names, task queue names, timers and
  headers are not encrypted**: Temporal must read them. A workflow id carries a ticket's key
  (`wi-<project>-<key>`), never its text; nothing may put a ticket's text in a search attribute.
- **No codec server for the Temporal UI.** With the codec on, the UI shows encrypted payloads; an
  operator who needs a payload decodes it with the key, outside the UI. Serving a codec endpoint is a
  separate decision (who may call it, under which identity, with which audit).
- **`tools/replay_record.py` refuses to archive with the codec on**: the CI replays the archives with no
  key, and an archive is reviewed in a PR. Archive demonstration histories from a deployment without it.
- **No refusal of clear text in production yet.** A production deployment without TLS or codec starts as
  before (the worker logs `tls` and `payload_codec` at start-up); refusing it would stop every running
  installation at upgrade. The refusal comes with phase 2, behind a version that says so.

## Phase 2: a key per organisation, from a KMS (the design path)

- The codec chooses the key by the payload's organisation. The organisation is known where the payload
  is made (the API starts a workflow for one organisation; a workflow's activities run for its
  organisation), so the converter is given a context: the SDK's serialization context (workflow id and
  namespace) maps to an organisation through the workflow's input, or one namespace per organisation
  carries it outright. Which of the two is the first decision of phase 2.
- The key in the metadata becomes `org/<id>/<version>`; the data key is wrapped by the organisation's key
  in a KMS (envelope encryption) and cached in the process for a bounded time, so the KMS is not on every
  payload's path. A KMS outage then stops new work for that organisation only.
- Crypto-shredding: destroying an organisation's key makes its histories unreadable, which is what an
  erasure of the organisation asks for.
- The provider (a cloud KMS, Vault Transit, an HSM) is chosen with the customer that needs it; the seam is
  a key resolver the core declares and an edition or a plugin implements.

## Consequences

- A new place that needs a Temporal client calls `connect_temporal`; the architecture test turns red
  otherwise.
- Turning the codec on is one-way for the histories it writes: a process without the key cannot read
  them, and neither can the replay CI. Losing the key loses the histories written under it.
- The codec costs one AES-GCM pass per payload; the payloads are small (ids and summaries, ADR 0008
  keeps big bodies out of Temporal), and no measurement says it matters.
