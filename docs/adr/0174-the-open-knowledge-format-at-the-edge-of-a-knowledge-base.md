# 0174 — The Open Knowledge Format at the edge of a knowledge base, never its store nor its trust

- **Status**: accepted, 2026-11-16; S72-01 implements points 1 to 5, S72-02 (export) is planned
- **Concerns**: `apps/api/src/choregos_api/knowledge/okf.py`, `knowledge/serving.py` (`files_of`,
  `_ready`, `file_name`), `knowledge/index.py` (`index_document`), `knowledge/conversion.py`
  (`title_of`), `services/conversation_knowledge.py`, `packages/runner/src/choregos_runner/knowledge.py`;
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
6. **Export is planned, not delivered (S72-02).** A base, and a memory's accepted facts (where an
   accepted proposal is exactly `verified: human:<id>`), exported as an OKF bundle make what an
   organisation curated portable — reversibility for an Apache-2.0 platform. The export is lossy for a
   memory (OKF has no bi-temporal validity; `valid_to` maps at best to `status: deprecated`) and carries
   no rights: it is a gesture of someone who may read everything exported, recorded in the audit.

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
- Not covered: the console does not show a document's OKF status or staleness; the eval set's
  recall@5 does not distinguish deprecated documents. `stale_after` is served, never acted on.
