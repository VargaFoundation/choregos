---
title: Write a skill
description: Package what an agent should know how to do as a skill, add it to your organisation, and give it to agents.
sidebar_position: 1
---

# Write a skill

Your team writes release notes its own way: one line per change, the ticket in brackets, breaking
changes first. Instead of repeating that in every agent's instructions, write it once as a **skill**:
a folder with a `SKILL.md` that says when to use it, and the files it brings. Any agent that carries
the skill reads it when the task calls for it.

**What you will be able to do:** write a skill, add it to your organisation from a zip or a git
repository, publish its next versions, and give it to an agent.

**Before you start:**

- the right to manage your organisation's library (an organisation administrator);
- an agent to give the skill to: see [Build an agent](agents.md).

:::note A skill grants nothing
A skill is knowledge, not permission. It cannot open a tool, a host or a file: `allowed-tools` is
refused. What an agent may call is decided by its version and its project ([Tools](tools.md)).
:::

## 1. Write the folder

```text
release-notes-style/
├── SKILL.md
└── examples/good.md
```

`SKILL.md` starts with YAML front matter: `name` (lower-case letters, digits and `-`, the skill's
slug) and `description` (what an agent reads to decide whether to use it). The rest is plain markdown.

```markdown
---
name: release-notes-style
description: How the billing team writes release notes. Use it whenever you write or review release notes.
---

# Release notes, the billing team's way

- One line per change, in the imperative: "Add credit notes", not "Added".
- The ticket in brackets at the end: `(FIN-42)`.
- Breaking changes first, under a "Breaking" heading, each with what the reader must do.
- No internal names: say what the customer sees.

`examples/good.md` shows a release that follows these rules.
```

Limits: 256 files and 5 MiB at most. Paths must stay inside the folder, symbolic links are refused,
and the only binary files taken are images and fonts (512 KiB each at most).

## 2. Add it to your organisation

**From a zip, in the console:** zip the folder, then in the sidebar **Skills**, card **Import a
skill**, choose the archive. The same by API:

```bash
cd release-notes-style && zip -r ../release-notes-style.zip . && cd ..
curl -s "$CHOREGOS_API_URL/api/v1/orgs/acme/skills/import" -H "Authorization: Bearer $CHOREGOS_TOKEN" \
  -H 'Content-Type: application/zip' --data-binary @release-notes-style.zip
```

**From a git repository:** in **Skills**, card **Import from a repository**, give the repository, the
branch, tag or commit, and the folder, then **Preview**: Choregos reads the skills it finds, and
**Import** takes them at the exact commit shown. Public repositories on `github.com` and `gitlab.com`
are read; your organisation keeps a list of allowed sources. Choregos can later tell you that a new
commit changes the skill (**Check for an update**); it never applies an update by itself.

**From the library:** the **Library** tab of **Skills** lists the skills shipped with the platform;
**Install** adds one.

**What you should see:** the skill under **Installed**, version 1, with its gate (below).

## 3. Know the review gate

Every version is rated when it is added:

| Risk | When | What happens |
| --- | --- | --- |
| `low` | a `SKILL.md` alone | usable at once |
| `medium` | references or assets | usable at once |
| `high` | a script, an executable file, an outbound URL, a credential-like string, or an MCP reference | waits until **another** administrator approves it (**Approve v1**); no agent can use it before |

## 4. Publish the next version

A version never changes. Import the folder again (the zip, or a new commit): Choregos publishes the
next version of the skill whose `name` matches. Agents that name the skill without a version take the
latest one at their next run; an agent that pinned a version keeps it.

## 5. Give it to an agent

In the agent's page, section **Skills**, add `release-notes-style`; or in the agent's spec:

```json
{ "skills": [{ "slug": "release-notes-style" }] }
```

Add `"version": 2` to pin one version. At run time the runner lays the skill's files where the
agent's backend reads skills (for Claude Code, `.claude/skills/`) and checks their digest first.

## What can go wrong

| Message | Why | Fix |
| --- | --- | --- |
| `SKILL.md must start with its YAML front matter` | no `---` block at the top | add the front matter |
| `` `description` is missing `` | an empty or absent description | describe when to use the skill |
| `a skill declares no permission (allowed-tools)` | the front matter has `allowed-tools` | remove it; grant tools on the agent |
| `` SKILL.md is named `x`, the skill `y` `` | a new version whose `name` differs | keep the same `name` |
| `422` with `skill.slug_reserved` | `library` and `catalogue` are reserved names | rename the skill |
| `409`, *already exists* | creating a skill whose name is taken | publish its next version instead |
| `409`, `egress_required` (git import) | the repository's host is not open | an administrator allows it in the same step |

## Go further

- [Build an agent](agents.md) that carries the skill.
- [Package skills and agents as a plugin](plugins.md), to share them between organisations.
