# 0191 — A knowledge base reads the resources of an MCP server

- **Status**: accepted, 2026-11-18 (asked by the project lead; story S72-05)
- **Concerns**: `packages/adapters/src/tranibor_adapters/documents/mcp.py`, `mcp/client.py`
  (`list_resources`, `read_resource`), `apps/api/src/tranibor_api/knowledge/sync.py` (`_mcp_source`),
  `routers/knowledge/source_config.py`, `mcp/resources.py` (the door's documentation as resources);
  extends [ADR 0051](0051-knowledge-bases.md) (a base's sources);
  keeps [ADR 0079](0079-choregos-and-graal-are-autonomous-products.md) (products meet through public
  interfaces), [ADR 0174](0174-the-open-knowledge-format-at-the-edge-of-a-knowledge-base.md) (a
  document's own header) and [ADR 0081](0081-knowledge-base-rights.md) (rights)

## Context

A knowledge base is fed by uploads and by four kinds of sources: SharePoint, Confluence, git, the web.
The knowledge a team wants its agents to read increasingly lives in another platform: a data
platform's catalogue (what a table holds, who owns it, how a figure is computed), a data dictionary, a
product's reference pages. Such a platform already publishes an MCP server, and the organisation
already declares MCP connectors for its runs (ADR 0034). The project lead asked on 2026-11-18 for
external knowledge bases that such a platform could feed, graal being the first example.

ADR 0079 decides how: Tranibor works with no data platform deployed, and meets one only through its
public interfaces. A source that names one product, or a client of its API, is refused by
`tests/licences/test_independent_products.py`. The generic interface is MCP's **resources**:
`resources/list` says what a server holds, `resources/read` gives each one, and any server may offer
them.

Three shapes were weighed (see the rejected alternatives); the project lead chose the pull.

## Decision

1. **A knowledge source of kind `mcp` reads the resources of an organisation's MCP connector.** It is
   a source like the others: a schedule (interval, cron, manual), a summary of what each sync added,
   updated, deleted or skipped, the same conversion, the same limits (documents per base, bytes per
   document), the same rights. It names a connector of kind `mcp` and of type `mcp` or `mcp-gateway`;
   its configuration is an optional `uri_prefix` and optional `mime_types` (at most 20). Nothing in
   the code names the server's product.
2. **The connector's own identity, never a person's.** A sync acts for nobody: it calls the server
   under the connector's `service` identity, and a connector that answers people only is refused at
   the sync with that reason. Like a Confluence service account, everything that identity can read
   under the prefix becomes readable by the base's readers; the procedure says so.
3. **A resource is its URI; the listing is complete.** `resources/list` is followed to its last page
   (at most 1000, an error beyond, and a cursor the server gives twice is a loop, an error at once:
   a catalogue pages far beyond a tool list's 50); whatever the server no longer lists leaves the
   base at the next sync; a listing that fails fails the sync and deletes nothing. A resource gone
   between the listing and its read (`-32002`) is skipped and said; the sync goes on.
4. **Version: the server's word when it gives one, the content's digest otherwise.** A resource that
   declares `annotations.lastModified` is read again only when it changes. One that does not is read
   at every sync, and the platform's digest decides whether it changed: correct, and as costly as the
   server's resources are large. The procedure asks servers to declare it.
5. **Name and format.** A resource is stored under its `name` (or the last segment of its URI), made
   safe for a file name, with the extension its `mimeType` calls for, so that it is converted as what
   it is; an unknown `text/*` is text, any other unknown type is skipped as "format not converted".
   Two resources listed under one name both carry a short digest of their URI. A `text` content is
   UTF-8, a `blob` base64. A resource that answers several contents (a collection, each part under
   its own URI, as the protocol's reference server does) is one document: its JSON parts as one JSON
   array, its text parts one after the other; binary parts are not joined, the resource is skipped
   and said. A resource with an `http(s)` URI links to it; another links nowhere.
6. **The Open Knowledge Format header travels as for any source (ADR 0174).** A resource whose text
   opens with an OKF header is read with it: `title`, `sources`, `status: deprecated` (neither served
   nor searched). Its `verified` stays a claim (`source_verified`), whoever the server is.
7. **The network is the run's.** The organisation's ceiling is read at every sync against the route's
   host; a connector reached through the organisation's gateway is synced through it, signed as
   `knowledge-source:<name>`; a connector at an outpost is refused, its rendezvous carries tool calls
   only. Adding the source opens the connector's host for the workers that sync it (the connector was
   opened for the API's calls only), through the same egress gate as any source.
8. **The platform's own door publishes its documentation as resources.** The pages `docs_search` and
   `docs_read` serve (ADR 0150) are also listed by `resources/list` and given by `resources/read`:
   the URI is the page's public URL, the `name` its slug, `lastModified` the file's, the text the
   page under an OKF header carrying its title. Any token that opens the door reads them, each call
   audited as `mcp_resource`. One instance's knowledge base can thus copy the documentation of
   another (or of itself), and it is the real server this source is proved against.

## Consequences

- A data platform feeds a base by publishing resources on its MCP server. Which resources (table
  descriptions, a dictionary, lineage summaries) and in which shape (markdown with an OKF header is
  best) is that platform's decision, in its own repository; nothing here waits for it.
- The base is a copy, refreshed on a schedule: what agents read is as current as the last sync, and
  is served and indexed like any document. Live figures are not knowledge; they stay a tool call
  through the broker.
- A server with thousands of unversioned resources is listed in full at every sync, but read only
  up to the base's limit of documents (`CHOREGOS_KNOWLEDGE_MAX_DOCUMENTS`, 500 by default): a
  resource beyond it is skipped without being read. The schedule is the owner's other lever.

## Rejected alternatives

- **The platform pushes documents** (an endpoint taking an OKF bundle, with a token scoped to one
  source). It fits a platform that knows when its knowledge changes, and it adds a contract, a token
  scope and a write path into a base. It stays possible later, as a second source kind; nothing here
  prevents it.
- **No copy: the agent searches the platform live.** That is a tool call of a catalogue connector,
  which the broker already serves, under its grants; it is not a knowledge base (no conversion, no
  index, no citation of a stored document, no restriction of what a run then writes).
- **A tool convention** (`list_documents`, `read_document`) instead of resources. It would be ours,
  not MCP's: every server would have to learn it, and a read-only listing would go through the
  governed path of tool calls.
- **A client of one platform's API in `packages/adapters`.** Refused by ADR 0079.
