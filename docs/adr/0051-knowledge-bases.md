# 0051 — Knowledge bases: a curated corpus, converted to markdown, served as files or as a hybrid index

- **Status**: accepted, 2026-10-12
- **Concerns**: `apps/api/src/choregos_api/knowledge/`, `routers/knowledge.py`, the `knowledge_*`
  tables, the runner's workspace, the `choregos-tools` sidecar; keeps
  [ADR 0007](0007-optional-memory.md) (memory is optional) and
  [ADR 0026](0026-what-the-core-rls-covers.md) (every organisation table under forced RLS)

## Context

Specialist agents (an architect, a security reviewer, a compliance officer) are only as good as the
reference they apply: an organisation's architecture framework, its security policy, its network
zoning rules. Those live in Word documents, slide decks, spreadsheets and PDFs. Choregos had no way
to give them to an agent: the memory (Ecphoria, or the lexical fallback) holds *facts learned from
work*, proposed by agents and accepted by humans — not a corpus a human curates.

Nothing here is "trained": a knowledge base is **ingested** (converted) and, when it is large,
**indexed**. Fine-tuning is out of scope.

The evidence of 2026 on retrieval is consistent: under roughly 200k tokens, put the corpus in the
context or let the agent search files; above, hybrid retrieval (BM25 + embeddings over contextual
chunks) is the robust default. A dedicated vector database is not needed: PostgreSQL 16 is already
there (D10), with full-text search built in and `pgvector` one extension away.

## Decision

1. **A knowledge base is an organisation object**, distinct from the memory. `knowledge_bases`
   (slug, name, scope, serving mode, status) and `knowledge_documents` (source, file name, title,
   mime, size, sha256 digest, source bytes, markdown, status, error) are under forced RLS like every
   other table of an organisation. Memory and knowledge bases stay apart: a knowledge base is
   read-only for agents; memory takes proposals.
2. **Scope** narrows who uses a base inside its organisation: `org` (every member reads it, every
   project's runs are served it), `projects` (members who can read one of the listed projects; runs
   of those projects only) or `groups` (humans of the listed IdP groups — a run carries no group, so
   it is never served a group-scoped base). Administrators of the organisation (`agent:manage`)
   curate and see every base. A base outside the caller's scope answers 404, not 403. The console
   session does not carry IdP groups yet (members by group is plan item 9): until it does, a
   group-scoped base is visible to administrators only.
3. **Upload, then convert in the background — never inside the request.** An upload (docx, pptx,
   xlsx, pdf, md, html, txt; 20 MiB by default) is checked on its bytes (the extension and the
   magic agree; an Office archive's central directory is read to refuse a zip bomb before anything
   inflates it), stored, and answered 202 `pending`. A background task of the API claims the row
   (`pending → converting`, by a conditional UPDATE: one replica only), runs the converter in a
   **child process** with a timeout and a bare environment (no database URL, no key reaches a
   parser of a hostile document), and writes `ready` with the markdown or `failed` with the reason.
   A replica that stops mid-conversion leaves `converting` rows; the next start-up puts the stale
   ones back to `pending`. The same bytes twice are one document.
4. **MarkItDown (MIT), pinned**, is the converter: light, and it keeps what an agent needs — the
   headings of a Word document, one `#` title per slide, a table per sheet, tables as tables.
   Docling converts PDFs better (layout, headings, OCR) at the cost of a much heavier image; a PDF
   here yields its text without its headings, and a scanned PDF fails ("needs OCR"). Images are not
   described.
5. **Two serving modes, chosen per base**:
   - `files`: the converted markdown is mounted **read-only** in the run's workspace under
     `.choregos/knowledge/<slug>/`, with an `INDEX.md`; the prompt says where it is. The agent greps.
     Enough for a framework of a hundred documents.
   - `index`: documents are cut into **contextual chunks** (each chunk prefixed by its document's
     title and heading path), embedded through the LiteLLM gateway (the deployment's embedding
     profile; a deterministic fake embedder under `CHOREGOS_FAKES`), and searched by **hybrid
     retrieval**: PostgreSQL full-text rank and pgvector cosine distance, fused by reciprocal rank.
     It is exposed to agents as the `knowledge_search` tool of the `choregos-tools` sidecar, which
     answers **citations** (document, heading path, excerpt) and only from bases served to the run's
     project.
6. **pgvector is used when it is there, and its absence is said.** Its extension is not trusted:
   a non-superuser cannot create it. The migration creates it when it can, and adds the vector
   column only if it exists; otherwise the index serves **full-text only**, and the knowledge base
   page and the search answer say so (`mode`). On SQLite (development, tests) the same ranking runs
   in the process.
7. **Each base has an eval set** — questions and the documents expected to answer them — and its
   recall@5 is computed on demand and shown on the base's page.

## Consequences

- The API image grows by MarkItDown's dependencies (pandas, onnxruntime through magika, pdfminer):
  the price of converting Office formats faithfully without a separate service.
- Connectors (SharePoint, web crawl, git — S33-04) add sources to the same documents; distillation
  into a principles skill (S33-05) reads the same markdown.
- A tool name with a dot (`knowledge.search`, as the plan wrote it) is refused by model APIs whose
  tool names allow letters, digits, `_` and `-`; the tool is `knowledge_search`, like the sidecar's
  other tools.
- Operations must give the database pgvector for the hybrid mode: the CloudNativePG standard
  images carry it, and the extension must be created in the **application** database
  (`postInitApplicationSQL`, not `postInitSQL`, which runs in `postgres`).

## Update — 2026-10-13: sources (S33-04)

8. **A knowledge base syncs from sources**, kept apart from uploads: `sharepoint` (a site or a
   drive, SharePoint or OneDrive, through an organisation connector of capability `documents`),
   `web` (one https site) and `git` (an https repository).
   - **Graph with `Sites.Selected` only.** The Entra application holds the application permission
     `Sites.Selected` and is granted `read` on the chosen sites by a tenant administrator; never
     `Files.Read.All` nor `Sites.Read.All`, which would open the whole tenant. Its secret is a
     reference (`secret_refs`), resolved when the source opens. Sync is **delta**
     (`/drives/{id}/root/delta`, the `deltaLink` kept as the source's cursor).
   - **Web**: same scheme and host, under the start URL's path, `robots.txt` obeyed, depth ≤ 5 and
     pages ≤ 500; a host resolving to a private, loopback or link-local address is refused.
   - **Git**: https only, a branch followed or a commit pinned, files matched by globs (markdown by
     default); the worker image carries `git` for it.
9. **A document deleted at the source leaves the base and its index** at the next sync: Graph says
   so (`deleted` facet); a crawl or a commit lists everything, and what it no longer lists is gone —
   except when a page answered a server error: a 500 is not a deletion, and that sync deletes
   nothing.
10. **The sync is a Temporal loop per source** (`KnowledgeSync`), started by the API with the source
    and by the worker at start-up, woken by `sync_now`, ended by `stop` or a source removed. Its
    activity is idempotent (ADR 0008): the cursor moves only once the changes are applied, and an
    item whose version is stored is not fetched again. The page shows each source's last sync, what
    it did, and its last error.
## Update — 2026-10-13: distillation (S33-05)

8. **A base distils into a principles skill.** The catalogue's `knowledge-distiller` (or the
   organisation's version of it) answers principles from the converted corpus in one model call
   through the gateway; each must cite a document of the base and a section, or it is dropped and
   said. The skill (`SKILL.md`, `references/sources.md` with each document's digest) is published
   **`pending_review`** regardless of the supply-chain gate's tier (ADR 0050): machine-written
   guidance reaches no agent before a person approves it. The version's author is the agent
   (`agent:knowledge-distiller`), so the administrator who asked may approve it; the request and
   the documents used are recorded on the base (`distillation`).

## Update — 2026-10-15: a crawl never empties its source (S39-18a)

10. **An empty crawl is unreadable, not empty.** A full listing deletes what it does not list
    (decision 7 of the sources update), which made a crawl that suddenly lists nothing (the start
    URL now redirects, the site is behind a wall) a mass deletion of the source's documents. A web
    crawl that lists no page reports its start URL as `unreadable`, so nothing is deleted and the
    sync says why. A start URL that redirects off the allow-list is refused at creation.
    Redirects inside the allow-list are followed; `exclude` path globs bound the crawl; page names
    come from the whole path so two languages do not collide.

## Update — 2026-10-15: ownership, a project-owned base (S39-14)

11. **Ownership is a fact apart from reach.** `knowledge_bases.owner_project` (nullable) names the
    project a base was created from. Reach stays `scope`: a project-owned base is `scope: projects`
    naming its owner, so reading and serving a run (`served_to_project`) are unchanged. No new scope
    value: it would duplicate `projects: [x]` and every consumer of the scope (agent specs, the
    builder) would need a new branch for nothing. Ownership is explicit, never inferred from "one
    project in the list": an administrator narrowing an organisation base to one project must not
    hand its curation to that project's owners by accident.
12. **Who curates what.** `POST /projects/{id}/knowledge` (`project:write`, the project's owners)
    creates a base with `owner_project` set. The owners of `owner_project` upload and delete
    documents, add, edit, sync and remove sources, edit the eval set, reindex, distil, rename, change
    the serving mode and archive it (`can_curate`). They never change `scope`, `projects` or `groups`:
    that stays `agent:manage` (an organisation administrator), and an administrator who changes
    them clears `owner_project`, so the base becomes the organisation's. Organisation administrators
    keep every right, over every base. A base without `owner_project` (every base created from
    `/orgs/{org}/knowledge`) is curated by administrators only, as before.
13. **The console.** A project's *Knowledge* section lists what its runs are served and creates a
    base owned by the project; the organisation's registry (`/knowledge`) lists every base with its
    scope. The migration adds one nullable column: existing bases have no owner.

## Update — 2026-10-16: refresh schedule (S39-19)

15. **The loop is a timer; the source's row decides.** The `KnowledgeSync` workflow is unchanged: a
    pass runs `sync_knowledge_source`, then sleeps the `interval_seconds` the activity answers (at
    least 300 s), or wakes on `sync_now`. The activity reads the row and calls `decide_pass`: it
    syncs only when the pass is due, and otherwise answers `{"status": "not_due", "interval_seconds": …}`
    touching nothing. So a worker restart or a `continue_as_new` no longer syncs off-schedule, and
    the archived histories keep replaying (no `workflow.patched`).
16. **Three refresh modes per source** (`refresh_mode`): `interval` (every `interval_minutes`, 5
    minutes to 7 days, the previous behaviour and the default), `cron` (`refresh_cron`, five fields,
    read in `refresh_timezone`, an IANA name; at least 5 minutes between two fires, checked over the
    next 50) and `manual` (only on demand, the loop idles). A sync requested by a person
    (`sync_requested_at` later than `last_synced_at`) runs in every mode. A knowledge base has no
    refresh of its own: it is the union of its sources, each with its natural cadence (a crawl daily,
    SharePoint hourly); the console's "apply to all sources" is a convenience, and `POST
    …/knowledge/{slug}/sync` syncs every source of a base now.
17. **"Next sync" is stored** (`next_sync_at`), written by the activity after each pass, failed or
    not, and by the API when the refresh changes. It is an estimate: a worker down at the cron
    minute syncs at its next pass. Editing the refresh takes effect at once: the API recomputes
    `next_sync_at`, commits and signals `sync_now`; the woken pass is a reschedule unless a sync
    was requested.
18. **Cron is in-house** (`choregos_core.cron`: parse, next fire with `zoneinfo`, minimum gap); a
    local time skipped by the spring DST change does not fire, one that exists twice fires once.
    **Temporal Schedules** were rejected (a second mechanism beside the loop, with its own lifecycle
    on create, patch and delete, for no gain over an activity that reads the row), and so was changing
    the workflow to sleep until a timestamp (a patch and a new history for the same behaviour).

## Update — 2026-10-15: Confluence (S39-20)

19. **A Confluence Cloud space, or one page and its descendants, is a source** (`confluence`),
    through an organisation connector `documents/confluence` (a new adapter on the ADR 0034
    registry: no new seam). The connector holds a service account's email and API token
    (`api_token` is a secret field); a classic token is used as HTTP Basic against the site, a
    *scoped* token through `api.atlassian.com/ex/confluence/{cloud_id}`. OAuth 2.0 (3LO) is not
    built: it needs a registered Atlassian app and a refresh token that the platform rewrites, which
    is a later story (S39-20c).
20. **The generalised connector interface.** A `documents` connector opens a source from the
    source's own configuration, `source(config, *, key)` (`DocumentsConnector` protocol), instead of
    SharePoint's keywords; the API holds `CONNECTED_KINDS`, `SOURCE_CONNECTOR_TYPES` (which
    connector type serves which kind) and one config validator per kind.
21. **Delta by version, deletions by listing.** Each sync lists the space once (no bodies,
    cursor-paginated) and a sub-tree is computed locally from `parentId`. The item version is the
    page's `version.number`, so the engine fetches a body only when it changed; the listing is
    `full`, so a page no longer listed is deleted from the base. A listing that fails part-way raises
    instead of returning a partial list (which would read as deletions); a page that became unreadable
    between the listing and its fetch is skipped and said (`ItemUnreadableError`). Bodies are
    Confluence's rendered `export_view` (macros expanded), with the storage format reduced to text
    as the fallback; the existing converter makes markdown of it. Limit: 5 000 pages per sync.
22. **The account's view, not the reader's.** The source mirrors what the connector's account can
    read: a page restricted against it is invisible and so leaves the base. Page and space
    restrictions are **not** mirrored per Choregos user: who reads the base is decided by the base's
    scope (org, projects, groups). Everything the account can read in the chosen space or tree
    becomes readable by everyone the base is open to; the service account should be given read on
    the spaces to index only.

## Update — 2026-10-15: private git (S39-18b)

23. **A git source reads a private repository through a `documents/git` connector.** The connector
    holds a token or a GitHub App's credentials (secret fields, ADR 0058 once sealed). The
    credential reaches `git fetch` as `GIT_CONFIG_COUNT/KEY_0/VALUE_0` (`http.<host>/.extraHeader`),
    so it is in no argv, error message or `.git/config`, and it is pinned to the connector's host.
    A git listing that matches no file is unreadable, not empty (decision 10). Documents are named
    by their whole path.

## Update 2026-10-16 — nothing CPU-bound on the API's event loop (S39-26)

On the dev tenant (0.18.1), fourteen uploads into an `index` base pinned the single-CPU API pod and
the ingress answered 502 to unrelated requests for minutes: cutting chunks and ranking ran on the
API's event loop. Since S39-26:

23. **The converter is a child process at a low priority** (`nice 15`): under the pod's CPU limit
    the kernel serves the API's own threads first.
24. **What stays CPU-bound in the API runs in a worker process** (`knowledge.offload`: one spawned,
    niced worker, a timeout, replaced when it dies or hangs): chunking a document, and the in-process
    rankings of a search (BM25, cosine). A worker *thread* is not enough — pure-Python work holds the
    GIL and still delays every request of the process. Parsing the converter's answer and titling a
    document run in a thread. Only I/O awaits on the loop.
25. **One conversion at a time per replica by default** (`knowledge_conversion_concurrency: 1`): the
    API pod has one CPU, a second converter would only share it.

## Update 2026-10-21 — a workbook is read as figures (S42-09c)

26. **A `.xlsx` is read directly, not through MarkItDown.** MarkItDown goes through pandas, so empty
    cells became `NaN`, empty headers `Unnamed: 3`, a percentage `0.1234`, and a 100 000-row sheet was
    converted whole. The converter now reads the workbook with openpyxl (read-only, cached values):
    one `## <sheet>` per sheet (hidden ones marked), empty cells empty, numbers as their cell format
    shows them (percent, thousands, decimals, dates), at most `knowledge_xlsx_rows_per_sheet` rows
    (2000) per sheet and then `… N more rows not shown`; the same fact reaches people as
    `knowledge_documents.warnings` (S42-09e: one sentence per cut sheet, shown by the console, and a
    source's `last_summary.truncated` / `truncated_reasons`). The cap reaches the child by argv (its
    environment stays bare); a workbook openpyxl cannot read falls back to MarkItDown. Documents
    converted before keep their old text until converted again (`POST …/documents/{id}/convert`), which
    also shrinks the corpus. Speaker notes and table or chart text of a deck survive MarkItDown 0.1.8
    (regression test), so decks are unchanged.

