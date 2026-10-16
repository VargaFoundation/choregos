# Refreshing the shipped catalogues

The MCP server catalogue and the skills catalogue are files in the image,
generated from a reviewed seed and **recorded** answers of the official MCP Registry and GitHub (and, for skills, reduced tarballs of pinned commits)
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
4. Review `git diff` of `catalogue.yaml` (the console's demo data, `apps/web/src/mocks/mcp-catalogue.json`, is
   regenerated with it and checked by `make catalogues-check`): new versions, stars, and above all entries that changed
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

## The skills catalogue

`make catalogues-record` also re-pins the skills: each repository's `ref` in
`tools/catalogues/skills/seed.yaml` is resolved to a commit, GitHub's stars and archived flag are
recorded, and a reduced tarball of the commit (only the seed's skill folders and the licence files)
is kept under `tests/fixtures/catalogues/skills/`. Then read:

- `dropped <id>: <reason>`: a skill refused by the publisher list, the licence rule, the gate
  (a binary file, `allowed-tools`) or the exclusions. Fix the seed or delete the line.
- `note <id>: asked bundled, delivered by git`: the skill is not eligible to be vendored (a
  copyleft licence, or a high risk: a script, an executable bit, or any outbound URL).
- The diff of `catalogue.yaml` per skill: a new `sha` means upstream changed. **Read the diff of
  the bundled files** (`git diff packages/core/src/choregos_core/skill_catalogue/bundled/`): they
  are what the image will redistribute and what an agent will read. A new finding or a risk change is
  a reason to drop the skill, not to merge.
- `THIRD_PARTY_NOTICES.md` is regenerated with the bundled set.

To add a skill, add a line (`name`, `categories`, `delivery`), plus a `path` when a repository holds
two skills of that name; the publisher must already be in `publishers.yaml` (a new publisher is a
decision, with its reason, in the same pull request).
