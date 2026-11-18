# 0185 — The documentation site is built here and published on GitHub Pages of graalsystems/tranibor

- **Status**: accepted, 2026-11-18 (decided by the product owner on 2026-11-18: the products no longer
  belong to or point at the Varga Foundation); S80-09 implements it
- **Concerns**: `website/` (Docusaurus), `.github/workflows/docs-site.yml`, `docs/site/`, the
  publishing identity's `documentation`; amends [ADR 0150](0150-the-instance-serves-its-own-documentation-over-mcp.md)
  (the default documentation address) and [ADR 0156](0156-the-publishing-identity-is-one-file.md)
  (`documentation`)

## Context

The public user documentation (`docs/site/`, the S62 contract) was written here but built and served
elsewhere: a workflow of the Varga Foundation's documentation repository copied it at each release into
its own Docusaurus site, published at `https://docs.varga.foundation/choregos`. That address was the
default `CHOREGOS_DOCS_URL` of every installation, the console's Help link and the base of the
instance's documentation tools. The product depended on a site hosted by an organisation it no longer
belongs to, and which is removing every reference to it.

## Decision

1. **The site is built from this repository.** `website/` holds the Docusaurus project (the same
   version, theme and plugins as the site it replaces, including `llms.txt` and `llms-full.txt`).
   `website/scripts/import-docs.mjs` copies `docs/site/` into `website/docs/` (ignored by git), leaves
   every `README.md` out and rewrites a link that leaves `docs/site/` to its GitHub address at the
   built commit or release tag; a link to a file that does not exist fails the build.
2. **It is published on GitHub Pages**, `https://graalsystems.github.io/tranibor/` (base URL
   `/tranibor/`, the pages at the root: `/use/work-items`, not `/choregos/use/work-items`).
   `.github/workflows/docs-site.yml` builds on every pull request that touches the pages or the site,
   and publishes on a push to `main` that touches them and on a release.
3. **The publishing identity's `documentation` is that address** (`https://graalsystems.github.io/tranibor`),
   and `tests/docs/test_docs_site_pipeline.py` refuses a site whose URL and base URL differ from it.
   A custom domain is a later change of one value (and a `CNAME` file in `website/static/`).
4. **The old address is not redirected from here.** It belonged to the Varga Foundation's site. An
   installation that set `CHOREGOS_DOCS_URL` keeps working; one that relied on the default gets the new
   one at its next upgrade.

## Consequences

- No product page is hosted by a third party. The site's content, theme and publication move with the
  repository.
- The pages keep their relative paths, so the console's and the API's deep links only change base.
- The first publication needs Pages enabled with the *GitHub Actions* source on the repository
  (`gh api -X POST repos/graalsystems/tranibor/pages -f build_type=workflow`).
