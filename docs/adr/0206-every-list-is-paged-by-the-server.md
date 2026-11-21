# 0206 — Every list the API returns is paged and filtered by the server, in SQL

- **Status**: accepted, 2026-11-19 (the product owner's request of the same day: *"pagination always
  server-side, in an ADR for every other subject"*).
- **Concerns**: every `GET` of `packages/contracts/openapi.yaml` that returns a collection; `apps/api`
  (`tranibor_api/keyset.py`, `deps.Page`), `apps/web` (every table over such a route). First applied to
  `GET /platform/gateway/keys` (`listGatewayKeys`), a `contract-change`. Builds on
  [ADR 0001](0001-frozen-contracts.md) (a response shape is a contract) and
  [ADR 0099](0099-the-api-is-a-dependency-that-only-shrinks.md) (a ceiling moves in the PR that moves it).

## Context

The product owner, on 2026-11-19, on **Administration → Platform**: the gateway keys are *"displayed a bit
raw, and neither paged nor filterable"*. Reading the route showed worse than a missing feature:

1. `GET /platform/gateway/keys` returned the **200 newest keys and nothing else**. The 201st key, and every
   older one, could not be read by any client. Nothing said so.
2. The route read **every project of the instance** to name the projects of those 200 keys.
3. The first fix proposed was a filter and a pager **in the browser**, over those 200 rows: it would have
   looked right and searched an arbitrary subset.

Counting the contract on the same day: **61** `GET` operations return a bare JSON array, with no cursor and no
limit. Seven are enumerations the code fixes (the executors, the backends, the shipped templates); the
others grow with use — runs, members, agents, memories, tokens, events. The routes that *are* paged
(`listAudit`, `listFindings`, the work items, the outposts' calls…) use `deps.Page.slice`: they read up to a
cap (1,000 rows for the audit) and cut the page **in Python**. Every page pays for the whole read, and what
lies beyond the cap is as unreachable as the 201st gateway key.

## Decision

### 1. A collection route returns a page

A `GET` that returns rows users or runs create returns `{items, meta}` — `meta` is `PageMeta`
(`next_cursor`, `has_more`) — and takes the contract's `Cursor` and `Limit` parameters (`limit` 1–200,
default 50). No bare array, no client-side cap.

### 2. The page is read in SQL, by keyset

`tranibor_api.keyset.keyset_page(session, query, sort=…, tiebreak=…, page=…)` orders by
`(sort DESC, tiebreak DESC)` — a timestamp, then the primary key, so rows of the same instant keep a total
order — and reads `limit + 1` rows from the database: the extra row says whether another page follows,
with no `COUNT(*)`. The cursor is the `(sort, tiebreak)` pair of the last row, encoded: opaque to clients,
stable when rows are inserted ahead of it (an offset is not), and served by an index. A cursor the API did
not write is refused (422).

`Page.slice` is **deprecated**: no new route uses it, and each one migrates to `keyset_page` when its
subject is next touched.

### 3. Filters are query parameters, applied in the same query

A filter the console offers is a parameter of the route, applied in SQL before the page is cut (a
substring is matched with `icontains(…, autoescape=True)`, so `%` and `_` are characters). Related data a
page needs (a project's slug, a user's name) is read **for the page's rows only**.

### 4. The console never pages or filters a server list in the browser

A table over a collection route sends its filters to the route and asks the next page by `next_cursor`
(`useInfiniteQuery` and a **More** button, as on the audit log). Filtering rows already received is
filtering an arbitrary subset; it is not done.

### 5. Exceptions are bounded by construction, and named

A list whose length is fixed by the code or the deployment (the executor kinds, the shipped templates,
the admin manifest's sections) may stay a bare array. It is named, with its reason, in `BOUNDED` of
`tests/architecture/test_lists_are_paged_by_the_server.py`. "Small today" is not a reason.

### 6. Two ratchets keep it

`tests/architecture/test_lists_are_paged_by_the_server.py`:

- every array-returning `GET` of the contract is in `BOUNDED` or in `NOT_YET_PAGED`; a new one fails. A
  route that becomes a page must leave `NOT_YET_PAGED` in the same PR (the test refuses a stale entry);
- the number of `page.slice(` calls in the routers may only go down (`SLICE_CEILING`).

At this ADR: 7 bounded, 53 not yet paged, 8 in-memory pages.

## Consequences

- Each migration is a `contract-change` (the response shape changes) and breaks a client that read the
  array: the CLI, the MCP door and the console read the API through its contract, and their callers move in
  the same PR.
- A list that used to show "everything" now shows its first page; a screen that summed or counted the
  whole list needs an aggregate route, not a bigger page.
- Keyset paging gives no page numbers and no "last page": the console offers **More**. A total count, where
  one is needed, is a separate aggregate.
- Sorting a list by another column needs that column in the keyset (sort value, then id): a new sort is a
  new `keyset_page` call, not a client sort.

## Alternatives discarded

- **Offset pagination** (`?page=3`). Rows inserted at the head shift every page (a key minted while an
  operator reads page 2 shows one row twice); `OFFSET n` scans and discards `n` rows.
- **Paging in the browser.** What the console was about to do for the gateway keys: it filters whatever the
  route chose to return, and nothing tells the operator the list was cut.
- **Raising the caps.** 200 → 1,000 → 10,000 moves the cliff, makes every read slower, and keeps the rows
  behind it unreachable.
- **Migrating the 53 routes at once.** 53 contract changes and their clients in one PR, against "one story,
  one PR". The ratchet makes the debt visible and lets it only shrink.
