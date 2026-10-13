# 0049 — An artifact is typed, stored and traced

- **Status**: accepted, 2026-10-12
- **Concerns**: the artifact contract (`packages/contracts`, `artifact.schema.json`), the API and its
  object store, the runner's workspace, the work item page; builds on
  [ADR 0005](0005-adaptateurs-et-fakes.md) (every integration has a fake),
  [ADR 0008](0008-idempotence-et-run-id.md) (idempotence) and
  [ADR 0045](0045-la-provenance-d-un-signal.md) (hand-off receipts)

## Context

A step hands its work to the next one through its outputs. Until now an output was a value in
`StageResult.outputs` — a markdown string or a bit of JSON — copied into `WorkItem.documents` and
inlined in the next prompt as "## Input: name". That carried four limits:

- **no type**: a transition declared `outputs: [profiles]` and nothing said whether `profiles` was
  text, data or a file; the console could not choose a viewer, the runner could not check a file;
- **no files**: a slide deck, a spreadsheet, a SARIF report or a video had nowhere to go — the
  contract only carried strings, and the pod's workspace disappears with the pod;
- **no history, no producer**: `documents` kept the last value only, and only the hand-off receipts
  (ADR 0045) said which run had written it;
- **no integrity**: nothing checked that what is served is what was produced.

## Decision

1. **An artifact is a revision of a named output of a work item.** The contract
   (`choregos_contracts.Artifact`, `artifact.schema.json`) says: its `name`, its `type` (a key of the
   vocabulary below), its `kind` (`markdown`, `json`, `diagram`, `report`, `file` — how the platform
   handles it), its `media_type`, its `version`, its `digest`, its `size`, `produced_by` (the run,
   its stage and attempt, the registry agent and its version — ADR 0033) and `storage_url`.
2. **One vocabulary of types**, in the contracts so the workflow validator, the runner and the API
   agree: `markdown` and `json` are *inline*; `mermaid`, `sarif`, `text`, `csv`, `html`, `pdf`,
   `docx`, `xlsx`, `pptx`, `png`, `jpeg`, `gif`, `webp`, `svg`, `mp4`, `webm` and `file` are files.
   An inline output is a value of the result's `outputs`; a file output is a file the agent writes
   in `.choregos/outputs/`.
3. **Stored where it belongs.** An inline artifact keeps its value in the database (`artifacts`
   table, under forced RLS); a file keeps its bytes in the **object store** (S3 protocol — RustFS in
   development; an in-memory `FakeObjectStore` under `CHOREGOS_FAKES=1`). Object keys are
   content-addressed (`orgs/<org>/work-items/<id>/artifacts/<name>/<sha256>`), so writing the same
   bytes twice writes the same object.
4. **Only the API touches the object store.** A runner never receives object store credentials (no
   broad token in a runner): it hands its files to the internal API, under its run token, which
   hashes, stores and records them. The S3 adapter signs its requests itself (SigV4 over `httpx`):
   no cloud SDK in the core.
5. **The digest is the sha256 of the bytes served on download** — text as UTF-8, data as canonical
   JSON, a file as written — so anyone can check a download with `sha256sum`. **Every read
   recomputes it**; bytes that no longer match are refused, never served.
6. **A version is a distinct content.** The same bytes produced again — a replayed activity, a
   retry that rewrites the same specification — make no new version; a different content makes the
   next one. Recording is therefore idempotent (ADR 0008). A row is never modified.
7. **Traced.** Each revision names the run that produced it; the hand-off receipts (ADR 0045) say
   which runs read it. For a file, the receipt's digest is the artifact's digest. For an inline
   output, the receipt keeps the digest of the value's canonical JSON (unchanged since S25-04), and
   the two are linked by output name and run.
8. **Generation stays out of the core.** The platform types, stores, hashes and traces what an
   agent wrote; it does not render slides or videos. Office files come from skills run in the
   sandbox, images and video from models behind the gateway (whose cost the gateway meters, ADR
   0004) or from MCP connectors. No office or media library enters the core.

## Consequences

- `apps/api/tests/test_artifacts.py` proves decisions 1, 3, 5 and 6: an output becomes a typed
  revision traced to its run, the same content makes no new version, and an inline value or an
  object altered behind the platform is refused on read.
- `packages/adapters/tests/test_object_store.py` checks the SigV4 signature against the AWS
  reference example. The adapter is **not** proven against a live S3 server by the unit suite.
- A downstream run receives its declared inputs as files in `.choregos/inputs/` rather than only in
  its prompt (S31-03); a declared output that is missing fails the stage whatever its type (S31-02);
  binary deliverables are stored as file artifacts (S31-05); the work item page shows them (S31-04,
  S31-06).
- File artifacts are read whole into memory by the API, bounded by `CHOREGOS_ARTIFACT_MAX_BYTES`
  (200 MiB by default). Streaming large videos is left for later.

## Alternatives discarded

- **Everything in the object store, inline values included**: one more round trip for every prompt
  rendered, and a store to run before a single markdown output works. Inline values are small and
  already lived in the database.
- **A cloud SDK (boto3, aioboto3)**: a large dependency for two verbs; the signature is short and
  tested against the reference example.
- **The runner writes to the store directly**: it would need credentials scoped wider than its run,
  which a runner must never hold.
