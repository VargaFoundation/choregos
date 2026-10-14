# 0050 — Skills come from a pinned commit of an allowlisted repository and pass a supply-chain gate; a high-risk one waits for a second person

- **Status**: accepted, 2026-10-12
- **Concerns**: `skill_versions` (`source`, `risk`, `findings`, `review_status`), the skills routes (`import-git`, `skill-sources`, `…/approve`,
  `…/update`), the `skill:import` permission, the console's skills page; amends
  [ADR 0033](0033-agent-registry-and-skill-library.md) (skills are a library)

## Context

ADR 0033 made skills a library of the organisation, imported as a zip. People asked to import
"the useful skills on GitHub" directly. Public skills are a supply chain: a 2026 scan of 3,984
public skills found a critical issue in 13.4 % of them, and the first malware campaign through
skills used more than thirty. The documented attack is the **bait-and-switch**: a harmless skill
earns its place, then the branch it was taken from changes. An import that follows a branch, or an
update applied by itself, hands the agents to whoever controls that branch.

## Decision

1. **A git import pins a commit.** `POST /orgs/{org}/skills/import-git` takes a 40-hex commit
   SHA; a branch or a tag is refused (`422`). A separate preview call resolves a branch or a tag to
   its SHA and lists the skill folders under the requested path, each checked as a zip would be,
   so a person sees what they pin and picks the skills to import.
2. **Each version records where it comes from**: `skill_versions.source` = repository, SHA,
   folder and the licence found there (the skill's licence file, else the repository's).
3. **Only github.com and gitlab.com, over HTTPS, as a tarball.** No `git clone` of an arbitrary
   host, no credentials, port, query or fragment in the URL, a timeout, no redirect followed, a
   size cap on the tarball and on what is read from it. A link, a device or a file that is not UTF-8
   text refuses the skill that holds it. The network is behind a fetcher with a recorded fake
   (ADR 0005): no unit test reaches GitHub.
4. **Allowlisted sources per organisation.** `GET|PUT /orgs/{org}/skill-sources`; by default
   `github.com/anthropics/skills` and the GitHub owners of the repositories the organisation's
   projects are connected to. A new permission, `skill:import` (project owners and
   administrators), imports **new** skills from an allowlisted repository; another repository, or a
   new version of an existing skill — which changes what the agents carrying it read — needs
   `agent:manage`.
5. **An update is a proposal.** `GET /orgs/{org}/skills/{slug}/update?ref=` resolves the
   repository's reference and returns the SHA and the files added, removed and modified against the
   latest version. It writes nothing; a person applies it by importing that SHA.
6. **Every version passes a gate, whatever its door** — a zip, the console, git (S32-06):
   - a **risk tier**: `low` (a `SKILL.md` alone), `medium` (references, assets — other text
     files), `high` (a script, an executable bit, an outbound URL, a credential-like string, an MCP
     reference, or any critical finding of the scan);
   - a **static scan**: prompt-injection phrases (*ignore previous instructions*, *without telling
     the user*…), hidden or bidirectional Unicode, base64 or hex blobs and code that decodes then
     runs them, a download piped into a shell; each finding names its file and line;
   - a **licence check**: the skill's licence file, else the repository's, else an
     `SPDX-License-Identifier`. A missing or non-open licence is **flagged**, not refused — several
     skills of `anthropics/skills` (docx, pptx, xlsx, pdf) are source-available, and the reviewer
     decides.
7. **A high-risk version waits for a second person.** It is born `pending_review`; until an
   administrator who is **not its importer** approves it
   (`POST …/skills/{slug}/versions/{n}/approve`, audited as `skill.approve`), an agent version may
   not pin it (`422`), an agent carrying the skill unpinned reads its latest *approved* version,
   `prepare_stage` refuses a pin on it, and the runner's internal read refuses it (`409`).
   A version the platform ships itself (its agent catalogue, a template) is assessed and recorded
   like the others, and approved by `platform`.
8. **Limits raised to 256 files and 5 MiB** (ADR 0033 said 64 and 512 KiB): real skills carry
   `references/`, and the risk tier, not the size, now does the work.

## Consequences

- A skill no longer changes because its upstream branch did; following upstream is a decision a
  person takes with the diff in front of them.
- Private repositories are not fetched yet (no token is sent); a skill with a binary file (a font,
  an image) is refused, as for a zip.
- A version is judged by what it contains before any agent reads it; a high-risk one costs a second
  person's attention, which is the point. The scan is a set of patterns, not a sandbox: it finds
  the documented tricks, not every one — the runner's closed network and the digest check stay the
  last lines (ADR 0033).
- Outbound URLs make a skill `high`: many honest skills link to documentation and will need a
  second reviewer. Accepted: an URL in a skill is an instruction an agent may follow.
