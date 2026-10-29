---
id: source.web
title: Add a web site to a knowledge base
applies_to: {source: web}
audience: project_owner
version: 2
reviewed: 2026-10-28
egress: ["the site's host"]
fields:
  url: "the https address the crawl starts from"
  max_depth: "how many links deep to follow, at most 5"
  max_pages: "how many pages to read, at most 500"
  exclude: "path globs, each starting with /, that are neither fetched nor followed"
  include_documents: "also read the PDF, Word, PowerPoint and Excel files the pages link to (same host)"
  connector: "optional: a documents/web connector holding the credential of a site behind a login"
  headers: "optional plain headers sent with every request, Accept-Language for example; never a credential"
---

## Before you start

- The https address of the pages to index. The crawl stays on the **same scheme and host**, under the start
  address's path, obeys the site's `robots.txt`, follows at most 5 levels and reads at most 500 pages.
- A host that resolves to a private, loopback or link-local address is **refused**: a knowledge base is not
  a way into the platform's network. For an internal site, use a git repository or a connector.
- A site behind a login needs a `documents/web` connector, declared once by an organisation administrator:
  it holds the credential (a bearer token, HTTP Basic or a named header) as a secret.
- Read the site's terms: you are copying its content into your knowledge base.

## At the vendor

Nothing to create for a public site. If the site blocks automated readers, ask its owner, or use their feed
or repository. For a site behind a login, ask its owner for a read-only credential made for an automated
reader (see the `documents/web` connector's procedure).

## Permissions

Without a connector the crawl is anonymous: it reads only what any visitor reads. With one, it reads what
the connector's credential reads, and the credential is sent only to the connector's host.

## In Choregos

1. **Knowledge**, the base, **Add source**, kind **Web**.
2. `url`, `max_depth` and `max_pages`. For a single page tick **This page only** (depth 0, one page). Tick **Include linked documents** to also read the PDF, Word, PowerPoint and Excel files linked from the pages, on the same host (at most 5 MiB each).
3. For a site behind a login, open **Advanced: authentication and HTTP headers** and pick the login
   connector. Plain headers (`Accept-Language: fr`) go in **Extra HTTP headers**, one per line; a header
   that carries a credential is refused there.
4. Choose the refresh: every few hours, every day or every weekday at a time, chosen days of the week, once
   a month, a cron expression of your own, or manually. Web content changes: once a day is usual.
5. The API must be allowed to reach the site: the network egress allowlist must open its host.

## Test it

Press **Sync now** and open the documents that were added.

## When it fails

- "resolves to a private address": not crawled, by design.
- Nothing indexed: `robots.txt` disallows the paths, or the start page links nowhere under its path.
- A redirect to another host: the crawl does not follow it, and the base is not emptied.
- A redirect to a login page when the source is added: the site needs a login connector, or the
  connector's credential is wrong or expired.
- A timeout: the egress allowlist does not open the site.
