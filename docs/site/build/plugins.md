---
title: Package agents as a plugin
description: Bundle agents, skills, templates and their checks in one versioned package, validate it with the CLI, and install it in an organisation.
sidebar_position: 3
---

# Package agents as a plugin

Your release-notes agent works well in `billing-api`; the payments team wants it too, in its own
organisation. A **plugin** packages agents and what they need (skills, project templates, evals,
setup procedures, MCP servers) in one versioned, digest-identified package that any Choregos
organisation can install.

**What you will be able to do:** lay out a plugin, validate and pack it with the CLI, publish it in a
source your instance trusts, and install, upgrade or uninstall it in an organisation.

**Before you start:**

- the [CLI](../cli/install.md); `choregos plugin` works offline, no instance needed;
- an agent and a skill you want to share ([Build an agent](agents.md), [Write a skill](skills.md));
- to install: an organisation administrator, and a plugin source your operator declared.

## 1. Lay it out

```text
billing-team/
├── plugin.yaml
├── agents/release-notes.yaml
└── skills/release-notes-style/SKILL.md
```

The manifest says what the plugin is and exactly which files it ships. Nothing else in the folder is
read, validated or packed:

```yaml title="billing-team/plugin.yaml"
apiVersion: choregos/v1
kind: Plugin
metadata:
  name: billing-team
  version: 1.0.0
  publisher: ACME billing team
  license: Apache-2.0
  description: The billing team's release-notes writer and its style guide.
  core: ">=0.25.0"
requires:
  plugins: []
  connectors: {}
contents:
  agents: [agents/release-notes.yaml]
  skills: [skills/release-notes-style]
```

`metadata.version` is semver and a published version never changes; `core` is the range of Choregos
versions the plugin was checked against. `contents` may also list `templates`, `evals`, `procedures`,
`servers` and `matrix_cases`.

An agent is a catalogue entry: what the catalogue shows (`catalogue`) and the agent itself (`agent`,
the same keys as when you [create one](agents.md#1-create-the-agent)):

```yaml title="billing-team/agents/release-notes.yaml"
catalogue:
  version: 1
  role: release_notes
  summary: Drafts the release notes of a change in the team's style.
agent:
  slug: release-notes
  display_name: Release notes writer
  description: Drafts the release notes of a change from its ticket.
  spec:
    instructions: |
      Write the release notes of {{ ticket.title }}. Follow the release-notes-style skill.
      Write them to release_notes.
    model: profile:standard
    runtime: { kind: prompt }
    limits: { max_turns: 20, max_minutes: 10 }
    skills:
      - { slug: release-notes-style }
```

A skill is a folder with its `SKILL.md`, as in [Write a skill](skills.md):

```markdown title="billing-team/skills/release-notes-style/SKILL.md"
---
name: release-notes-style
description: How the billing team writes release notes. Use it whenever you write or review release notes.
---

# Release notes, the billing team's way

- One line per change, in the imperative: "Add credit notes", not "Added".
- The ticket in brackets at the end: `(FIN-42)`.
- Breaking changes first, under a "Breaking" heading.
```

## 2. Validate and pack it

```bash
choregos plugin validate billing-team/
choregos plugin pack billing-team/ -o billing-team-1.0.0.tar.gz
```

**What you should see:** `✓ billing-team 1.0.0 sha256:…`. The **content digest** identifies the plugin:
the same declared files always give the same digest and the same archive, whatever their dates or
permissions on disk.

`validate` reports every problem with its file: a path that leaves the folder or goes through a
symbolic link, a manifest key it does not know, an agent without `catalogue.role`, a skill folder with
no `SKILL.md`, a template, eval or server that does not match its schema. It also warns, without
refusing, about an agent that reads a knowledge base the plugin does not ship.

## 3. Publish it in a source

Your instance installs plugins only from the **sources** its operator declared, each with how it is
trusted (a signature identity, a key, or an offline directory). A source is an index of plugins,
published in an OCI registry or a git repository pinned to a commit. Write the index for a folder of
plugins:

```bash
choregos plugin index plugins/ -o index.yaml --oci-repository ghcr.io/acme/choregos-plugins
```

or, for plugins read from a git repository at an exact commit (a branch or a tag is refused, it can
move):

```bash
choregos plugin index plugins/ -o index.yaml --git-url https://github.com/acme/agents \
  --git-commit 0123456789abcdef0123456789abcdef01234567 --git-path plugins
```

Declaring the source and its trust is your operator's part:
[plugin sources](../../plugins.md#sources-and-trust).

## 4. Install it in an organisation

**In the console:** sidebar **Plugins** lists the sources, what each offers, what is installed, and
the requests waiting for a second administrator. Choose the plugin and its version, then install.

**By API:**

```bash
curl -s "$CHOREGOS_API_URL/api/v1/orgs/acme/plugins/install" -H "Authorization: Bearer $CHOREGOS_TOKEN" \
  -H 'Content-Type: application/json' -d '{"source": "acme", "name": "billing-team", "version": "^1"}'
```

Before anything is written, every agent is validated as if created by hand, every skill passes the
skill checks, every eval suite is played and must pass. Any refusal leaves the organisation unchanged
(a `422` naming the check).

**What you should see:** the agent `release-notes` and the skill `release-notes-style` in the
organisation's library, marked as installed by the plugin; a project can now use the agent in a
workflow.

A plugin that brings an MCP server, or a skill rated high risk, **waits for a second administrator**:
the answer is `202` with `"status": "pending_approval"` and the reasons; another administrator approves
it in **Plugins**.

## Upgrade and uninstall

- **Upgrade** publishes the next version of an agent or a skill only when its text changed. One your
  organisation changed since is offered the update, never overwritten. Project pins do not move.
- **Uninstall** removes what the plugin installed and nobody changed or pins; the rest stays as the
  organisation's own, and the answer lists what was removed and what was kept.

## Go further

- A plugin can also ship a conversational assistant: [Ship an assistant in a plugin](assistants.md).
- The full plugin format, servers and trust:
  [plugins reference](../../plugins.md).
- [CLI reference](../cli/reference.md#choregos-plugin) for every option of `choregos plugin`.
