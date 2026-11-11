---
title: Knowledge
description: Give agents documents to read, from uploads, Confluence, SharePoint, git or the web, and decide who may use and change them.
sidebar_position: 5
---

# Knowledge

A knowledge base is a set of documents a person curates and agents only read: an architecture
framework, security rules, runbooks, a product's documentation, a few decks and spreadsheets. This page
shows how to create one, feed it, keep it current, and decide who uses it.

**What you will be able to do**

- Create a knowledge base for one project, or for the whole organisation.
- Feed it with uploads and with sources that sync on their own.
- Choose how runs read it: as files, or as a searchable index.
- Decide which runs are served it and who may change it.
- Keep a knowledge base as code, in a file you review.

## Before you start

- For a project's own knowledge base: **Project owner** on that project.
- For an organisation-wide knowledge base, or one shared by several projects: **Organisation admin**.
- To sync from Confluence or SharePoint: a connector of that kind, declared by an organisation admin
  (see [Connectors](connectors.md)).

## Knowledge or memory?

| | Knowledge base | Memory |
| :-- | :-- | :-- |
| Holds | Documents a person chose | What agents learnt from work |
| Written by | People: uploads and synced sources | Runs propose, people accept |
| Agents | Read only | Read, and propose |

For a handful of facts only one work item needs, give the document as an input of the work item
instead of keeping a base that must be synced.

## Create a knowledge base

### For one project

1. Open the project, then **Knowledge** in the sidebar. It lists the project's own bases, then those
   shared with every project.
2. Press **New knowledge base**, give it a name, and create it. It is served to this project's runs
   only, and the project's owners curate it.

### For the organisation

1. Open **Knowledge** in the organisation's sidebar. It lists every base, with who uses each one.
2. In **Create a knowledge base**, choose who it is for: the whole organisation, a list of projects, or
   the people of some identity-provider groups (a group's base is for people, never for a run).

## Feed it

Open the base. Its status banner says whether it can be used now: **Ready**, or still converting or
syncing, with a progress bar.

- **Upload documents**: Word, PowerPoint, Excel, PDF, HTML, Markdown, text, YAML, JSON, TOML, up to
  20 MiB each by default. Each file is converted to text in the background; uploading a file with the
  same name replaces it.
- **Add a source** that syncs on a schedule: a **Confluence space or page tree**, **SharePoint /
  OneDrive**, a **git repository** or a **web site**. For a web source, tick **This page only (no links
  followed)** for one address, and **Include linked documents (PDF, Word, PowerPoint, Excel)** to read
  the files it links to.

Each source shows when it last synced, what it added, updated, deleted or skipped, and its last error.
A document deleted at the source leaves the base at the next sync.

What conversion keeps: headings, slide titles and speaker notes, tables, each sheet of a workbook as a
table with the values Excel last saved. What it loses: **images and charts are not described**, and
there is **no OCR**, so a scanned PDF fails.

## Choose how runs read it

On the base's page, the **How runs read it** card sets the **Serving mode**:

| Mode | How it works | Use it when |
| :-- | :-- | :-- |
| Files | The ready documents are laid read-only in the run's workspace; the agent reads them like any file | The text fits in a model's context: up to roughly 200,000 tokens |
| Index | The documents are cut into passages; the agent searches them and cites what it used | The base is larger, or you want citations |

To check an index, write a few questions with the documents expected to answer them (the **Eval set**)
and run it.

## Let an agent read it

A base is read by the agents that name it, in runs of the projects it is served to. The base's
**Who uses it** card lists the **Agents that read it** and the **Runs it may be served to**. To add one,
name the base in the agent's knowledge (see [Agents](../build/agents.md)). A run whose project is not
served a base its agent names does not start, and says why.

## Decide who may change it

Reach decides which runs are served a base. **Grants** decide which people may change it. On the
base's **Access** card, give a member, a group or a project's members a role:

| Gesture | Viewer | Editor | Owner | Organisation admin, or owner of the owning project |
| :-- | :-- | :-- | :-- | :-- |
| Read | yes | yes | yes | yes |
| Upload, add a source, sync, distil, rename | no | yes | yes | yes |
| Share, archive | no | no | yes | yes |
| Widen who it is served to, restrict it | no | no | no | yes |

A **restricted** base is read by people only through a grant, and served to a run only on behalf of
the person who started the work item. A work item started by a schedule or a tracker names no person,
so its runs stop before they start. What such a run writes stays visible only to the base's readers.

A sync from Confluence or SharePoint reads with a service account: everything that account can read
in the chosen space becomes readable by the base's readers. Give that account access to what you mean
to share, and nothing else.

## Turn a base into a skill

A base can be **distilled** into a skill of short principles, each citing the document it came from.
The page first shows an estimate (documents, model calls, cost) with no model call; you can leave the
biggest documents out. The distilled skill waits for an administrator's review before any agent
carries it.

## Keep a knowledge base as code

A manifest describes a base, its sources, its uploads and its eval set. Applying it makes the base
match the file; applying it again changes nothing.

```yaml
apiVersion: choregos/v1
kind: KnowledgeBase
slug: billing-docs
name: Billing documentation
description: The product's documentation and the finance team's rules, for the billing agents.
scope: projects
projects: [billing-api]
serving_mode: index
sources:
  - name: handbook
    kind: git
    config:
      url: https://github.com/acme/handbook.git
      ref: main
      paths: ["docs/**/*.md"]
    interval_minutes: 60
  - name: public-pricing
    kind: web
    config:
      url: https://acme.example/pricing
      max_depth: 1
    interval_minutes: 1440
uploads:
  - finance/*.xlsx
eval:
  - question: How is a credit note numbered?
    expected: [numbering.md]
```

```bash
choregos knowledge apply -f billing-docs.yaml --dry-run
choregos knowledge apply -f billing-docs.yaml
choregos knowledge sync billing-docs
choregos knowledge eval billing-docs
```

`--dry-run` prints the plan and changes nothing. Without `--prune`, sources and uploads the file does
not list are left alone. These commands need an organisation admin's token.

## What can go wrong

| Symptom | What to do |
| :-- | :-- |
| A document reads **Failed** | Read its reason: a scanned PDF, a file too large, a format the converter does not read. Fix the file and upload it again |
| A source cannot be added: the host is not allowed | The organisation must open that host for its network. An admin presses **Allow and continue**; anyone else presses **Ask an administrator** |
| A Confluence or SharePoint source cannot sync | The organisation has no connector of that kind yet; ask an organisation admin |
| A run stops before it starts and names a knowledge base | The run's project is not served that base, or the base is restricted and the work item names no person who reads it |

## Go further

- [Knowledge bases](../../usage.md#knowledge-bases), in depth: conversion, sources, distillation.
- [Skills](../build/skills.md): what a distilled base becomes.
