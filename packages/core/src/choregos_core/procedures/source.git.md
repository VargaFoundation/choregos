---
id: source.git
title: Add a git repository's documentation to a knowledge base
applies_to: {source: git}
audience: project_owner
version: 1
reviewed: 2026-10-16
egress: ["the git host"]
fields:
  url: "the repository's https address"
  ref: "a branch (followed) or a commit (pinned), main by default"
  paths: "glob patterns of the files to index, *.md by default"
---

## Before you start

- The repository's **https** address. A `git://` or `ssh` address is refused.
- This reads a **public** repository (or one reachable without credentials from the API). For a private
  repository, a documents connector that holds the credential is needed, when your version offers one.
- Choose what to follow: a **branch** is followed at each sync; a **commit** pins the content.

## At the vendor

Nothing to create for a public repository.

## Permissions

The source makes one shallow fetch of the ref and reads files. It reads at most 500 files of 5 MiB each.

## In Choregos

1. **Knowledge**, the base, **Add source**, kind **Git**.
2. `url`, `ref` and `paths` (globs; the default is markdown anywhere).
3. Choose the refresh. The cursor is the commit: the same commit is no change, and a file gone from the new
   commit is deleted from the base.
4. The egress allowlist must open the git host.

## Test it

Press **Sync now** and open the documents that were added.

## When it fails

- "a git source is fetched over https": the address is not https.
- Nothing indexed: no file matches `paths`.
- A fetch that fails: the ref does not exist, or the repository is private.
- A timeout: the fetch is cut at 5 minutes; the egress allowlist may not open the host.
