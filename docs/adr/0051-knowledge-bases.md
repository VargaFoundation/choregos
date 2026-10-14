# 0051 — Knowledge bases: a curated corpus, converted to markdown, served as files or as a hybrid index

- **Status**: accepted, 2026-10-12
- **Concerns**: `apps/api/src/choregos_api/knowledge/`, `routers/knowledge.py`, the `knowledge_*`
  tables, the runner's workspace, the `choregos-tools` sidecar; keeps
  [ADR 0007](0007-memoire-optionnelle.md) (memory is optional) and
  [ADR 0026](0026-ce-que-la-rls-du-coeur-couvre.md) (every organisation table under forced RLS)

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
