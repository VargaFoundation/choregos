# 0174 — The Open Knowledge Format at the edge of a knowledge base, never its store nor its trust

- **Status**: accepted, 2026-11-16; S72-01 implements points 1 to 5, S72-02 (contract) and S72-03 point 6;
  S72-04 point 7 (amended 2026-11-17): a memory's export, refused for Ecphoria
- **Concerns**: `apps/api/src/tranibor_api/knowledge/okf.py`, `knowledge/serving.py` (`files_of`,
  `_ready`, `file_name`), `knowledge/index.py` (`index_document`), `knowledge/conversion.py`
  (`title_of`), `services/conversation_knowledge.py`, `packages/runner/src/tranibor_runner/knowledge.py`;
  amends [ADR 0051](0051-knowledge-bases.md) §5 (what a `files` base lays down); keeps
  [ADR 0081](0081-knowledge-base-rights.md) (rights) and [ADR 0007](0007-optional-memory.md) (a young
  dependency earns its place)

## Context

The Open Knowledge Format (OKF, Google Cloud, Apache-2.0; v0.1 in June 2026, v0.2 on 2026-07-24,
`GoogleCloudPlatform/open-knowledge-format`) packages knowledge for agents as a directory of markdown
files, each opening with a YAML header whose only required key is `type`. It adds optional families:
`title`, `description`, `resource`, `tags`; provenance (`sources`, `generated: {by, at}`); trust
(`verified: [{by, at}]`, from which a consumer **derives** a tier — `human:<id>` makes a concept
"human-reviewed"); lifecycle (`status: draft | stable | deprecated`, `stale_after`); reserved
`index.md` and `log.md` in every directory; `okf_version` in the root `index.md`. Producers named by
the spec include Dataplex, Unity Catalog and Collibra exports.

A `files` base (ADR 0051 §5) was already almost a bundle: one markdown file per document and an index,
under `.choregos/knowledge/<slug>/`. What it lacked is what an agent needs to weigh a document: where
it comes from, when it was converted, whether it is current. And a `git` or SharePoint source could
already ingest an OKF bundle, but read its header as body text: a `deprecated` concept was served as
current, and an author's `verified: human:…` reached the agent as if it were a review.

The format is five months old and led by one vendor; adopting it as a store would tie the core to it.

## Decision

1. **OKF is read and written at the edge; the database stays the store and RLS the rights.** Nothing
   is added to the tables. `knowledge/okf.py` reads a document's header from its stored markdown when
   it is needed and writes one when the document is served.
2. **A document's own header is read when it comes in.** `split()` takes a leading `---` YAML block
   only if it is a mapping of at most 64 KiB; anything else stays text (OKF §11: a consumer refuses
   nothing). Timestamps stay the text they were written in. The header's `title` names the document
   when its format declares none, and a `#` comment of the header is never taken for a heading.
3. **A document that declares itself `deprecated` is neither served nor searched.** It is not pinned
   for a run (`_ready`), and `index_document` cuts no chunk of it. It stays in the base, visible in the
   console, and comes back if its source drops the status. The header is not cut into chunks either:
   it is metadata, not a passage.
4. **What an agent reads is an OKF bundle.** Every file a run lays down (and every document a
   conversation reads, ADR 0159) is served with one header: the document's own keys kept key for key
   (unknown ones too, OKF §11), completed with what the platform knows — `type` (`Document` when it
   names none), `title`, `resource` (the source URL), `generated: {by: process:choregos-knowledge, at:
   converted_at}`, `sources: [{resource, title: filename}]`. What the document said of itself wins
   over what the platform infers. The serving is deterministic: the run's digest
   (`knowledge_digest`, computed by the API when the run is prepared and by the runner before writing)
   is unchanged in shape, so **no contract changes**. The runner writes `index.md` (lowercase, OKF's
   reserved name) with `okf_version: "0.2"`, refuses a document delivered as `index.md` or `log.md`,
   and the API never names one so. The prompt says what `status`, `stale_after` and `source_verified`
   mean.
5. **A `verified` that came with a document is a claim, never the platform's word.** It is served
   renamed `source_verified`: nothing is lost, and no consumer derives a "human-reviewed" tier from a
   line whoever wrote the file could write (the platform's guarantees are mechanisms, principle 4).
   The platform writes no `verified` of its own today: nobody reviews a document of a base one by one.
   If that review exists one day, it is recorded by the platform and served as `verified`.
6. **A base exports as an OKF bundle, and its console shows each document's lifecycle (S72-02,
   S72-03).** `GET /orgs/{org}/knowledge/{slug}/export` answers a zip of one folder named after the
   base: `index.md` with `okf_version`, then every ready document with the header a run is served — a
   deprecated one included, with its `status`, since the export is the base, not what a run reads.
   Any reader of the base may take it (it holds nothing the base's reading does not show); no right,
   grant or source credential travels with it; it is audited (`knowledge.export`, with the number of
   documents and bytes). The archive is deterministic: the same base exports the same bytes. A
   document's `okf` field (`type`, `status`, `stale_after`, and `stale` computed when read) is read
   from the head of its markdown only (`okf.HEAD_CHARS`), so a listing never loads a corpus; the
   console badges a withdrawn, out-of-date or draft document and offers the download.
7. **A memory's export: its active facts, `verified` only where a person accepted one (amended
   2026-11-17, S72-04).** `GET /projects/{id}/memory/export` (`?memory=`, `?agent=`) and
   `GET /orgs/{org}/memories/{slug}/export` answer a deterministic zip, for whoever may search that
   memory, audited `memory.export`: one file per active fact of that memory and no other, its kind as
   `type`, its subject as `title`, its content as body, its provenance as `sources`, `valid_from` and
   `valid_to` kept as its own keys. OKF has no bi-temporal validity: a fact whose `valid_to` is past is
   `status: deprecated`, which is all it can say. The two facts this point waited for:
   - **the decider** is the audit log's: a `memory.accept` row by a user (not a token, not the
     system) on that fact. A decision records `found` from now on, and one that found nothing verifies
     nothing. A fact a run wrote, or one the auto-accept rule took, is exported without `verified`. A
     row written before `found` existed counts: the person did ask to accept that fact;
   - **the listing** is the adapter's `list_facts`, which the lexical memory has. Ecphoria does not:
     it has `GET /api/v1/memories`, but it does not read the `X-Ecphoria-Namespace` header Choregos
     sends (ADR 0075), so a project's named memories share its tenant, a configured tenant holds every
     namespace, and an organisation's global memories share `org:<org>`. A listing would mix
     memories; the export is refused (409) rather than approximated. The same gap affects what
     `search` and `context_pack` read on Ecphoria; it is Ecphoria's or ADR 0075's to close, not this
     point's.

## Alternatives rejected

- *OKF as the storage format of bases and memories*: the database holds digests, RLS, statuses and
  sync cursors OKF has no place for; a five-month-old, single-vendor format would become a core
  dependency (ADR 0007's reasoning).
- *Trusting an imported `verified`*: the tier would be the file author's word; a `git` source of a
  repository anyone can push to would make its content "human-reviewed".
- *Dropping an imported `verified`*: the information is useful to a reader as a claim; renamed, it is
  kept without the tier.
- *Storing the parsed header in new columns*: a migration for metadata the markdown already holds;
  reading it from the markdown keeps one source of truth.
- *Keeping `INDEX.md`*: on a case-sensitive file system it is not OKF's reserved `index.md`, and a
  bundle whose every non-reserved file must carry a header (OKF §11) would not conform.

## Consequences

- Every file of a `files` base changes (it gains a header): runs prepared before the deployment and
  laid down after it stop on a digest mismatch (exit 51), as for any base edited in between.
- The served header adds a few hundred bytes per document to a run's workspace and to a
  conversation's document read; `DOCUMENT_CHARS` now counts them.
- A document of an `index` base that declares itself deprecated drops out of `knowledge_search` at its
  next indexing (conversion, sync or reindex), not before.
- A conversation neither lists nor reads a deprecated document (`knowledge_list`, `knowledge_get`), as
  a run is not served it.
- A bundle synced from git keeps its documents but not its folders: a document lands under its own
  file name (ADR 0051), so a concept's bundle-relative link to another folder does not resolve in what
  a run is served or in the export. OKF tolerates broken links (§11); the text is intact.
- Not covered: the eval set's recall@5 does not distinguish deprecated documents; `stale_after` is
  served and shown, never acted on; a memory's export on Ecphoria (point 7).
