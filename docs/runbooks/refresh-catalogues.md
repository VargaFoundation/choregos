# Refreshing the shipped catalogues

The MCP server catalogue (and, from S39-12, the skills catalogue) are files in the image,
generated from a reviewed seed and **recorded** answers of the official MCP Registry and GitHub
([ADR 0065](../adr/0065-catalogues-of-mcp-servers-and-skills.md)). Vendors move fast: nine of the
first hundred seed repositories were archived within months. Refresh when a server is added,
when `make catalogues-check` turns red after a seed change, and about once a month.

## How to know it is this

- `make catalogues-check` (or the `lint` job) fails with a diff of `catalogue.yaml`.
- A replay raises `no recording for POST https://api.github.com/graphql … run the generator with
  --record`: the seed changed, so the GitHub query changed, so its recording is missing.
- A user reports an entry that no longer exists at the vendor.

## Add or change a server

1. Edit `tools/catalogues/mcp/seed.yaml`: `id`, `title`, `publisher`, an English `description`
   (300 characters at most), `categories`, `auth`, and where known the `registry` name and the
   `repo`. A hosted remote the registry does not list goes in `overrides.yaml` under `remotes`,
   with the vendor in `vendors` if it publishes the server itself. Probe the URL first: an MCP
   endpoint answers `200`, or `401` asking for credentials.
2. Record, with the network and a token that can read public repositories:

```bash
GITHUB_TOKEN=$(gh auth token) make catalogues-record
```

3. Read the lines `dropped <id>: <reason>`. A dropped line is the rules working; fix the seed or
   delete the line.
4. Review `git diff` of `catalogue.yaml`: new versions, stars, and above all entries that changed
   from installable to not, or lost `official`. Bump `catalogue_version` in `overrides.yaml`
   (CalVer, `2026.11.0`) when the refresh is meant to be released.
5. Open a pull request with the seed, the recordings under `tests/fixtures/catalogues/` and the
   regenerated file. A person merges it: nothing follows the registry by itself.

## Find candidates

```bash
GITHUB_TOKEN=$(gh auth token) uv run python -m tools.catalogues.build_mcp_catalogue --discover
```

prints repositories of the awesome list that are not in the seed, ranked by stars, and writes
nothing. Raw stars are polluted by repositories that are not MCP servers: read each one before
promoting it.

## How to check it is fixed

```bash
make catalogues-check && uv run pytest -q tests/tools packages/core/tests/test_mcp_catalogue.py
```
