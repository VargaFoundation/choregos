# The public user documentation: how to write it

This folder is the source of the public documentation of Choregos, published at
<https://docs.varga.foundation/choregos/>. This page is for the people who write it. It is not
published: the import into the documentation site leaves every `README.md` out.

The reference pages one level up (`docs/*.md`) stay what they are: complete, precise, written for
people who run and extend the platform. The pages here are for people who **use** it: a team lead, a
developer, an operator working in the console. They cut and rewrite; they never copy. When a reader
needs the full detail, link to the reference page.

## Where a page goes

The slugs are fixed by the S62 contract. Do not move or rename a page: the console and the API link to
these addresses (`CHOREGOS_DOCS_URL`, by default `https://docs.varga.foundation/choregos`).

| Folder | Section | Order |
| :-- | :-- | :-- |
| `index.md` | Overview: what Choregos is, for whom, a five-minute tour, the map | 1 |
| `concepts.md` | Concepts | 2 |
| `get-started.md` | Get started | 3 |
| `use/` | Use Choregos: the seven task pages of the contract | 4 |
| `build/` | Build agents: skills, agents, plugins, tools | 5 |
| `integrate/` | Integrate: MCP, A2A, triggers, REST | 6 |
| `cli/` | Command line: install, reference | 7 |
| `host/` | Host Choregos: deployment, security, upgrades (`host/index.md` is the section's page) | 8 |
| `reference/` | Reference: workflow YAML, errors, API (`reference/index.md` is the section's page) | 9 |

Each section folder has a `_category_.json` with its label and position. It is written once, here, for
every section, so the sidebar is in the right order whatever lands first. Do not add another one.

Order pages inside a section with `sidebar_position` in the front matter. Never set `slug` or `id`: the
URL is the file's path, and `tests/docs/test_site.py` refuses an override.

## Every page

Front matter, at least:

```yaml
---
title: Work items
description: One sentence that says what the reader can do after this page.
sidebar_position: 3
---
```

Then, in this order, with these headings where they apply:

1. One or two sentences: what the page is about.
2. **What you will be able to do**: a short list of outcomes, each a verb.
3. **Before you start**: the role you need, what must already exist.
4. The steps, as numbered lists. Name the real screen and the real button, in bold, as the console
   writes it: **Settings › Connections**, **New work item**, **Approve**. Give the command next to the
   screen when there is one.
5. **What you should see**: the state, the badge, the output that tells the reader it worked.
6. **What can go wrong**: the message they may meet, what it means, what to do.
7. **Go further**: links to the next page and to the reference.

## Style

- Plain English, short sentences, the second person ("you"). One idea per sentence.
- Task first: say what to do, then why, in one line. Leave the history of a decision to the ADRs.
- Use the words of the console. If the console says "work item", write "work item", not "ticket".
- One running example across the pages: a classic development project, `billing-api`, created from
  the `github-software-delivery` template, in the organisation `acme`.
- Say what is not done yet, plainly, rather than let the reader find out.
- English only: `tools/check_english.py` runs on these pages like on everything else.

## What the tests check

`tests/docs/test_site.py` turns CI red when a page drifts from the code:

- every contract slug has its page (the sections of the second half are checked once their folder
  holds a page);
- every relative link resolves, to a file and to a heading; a link to a section that has no page yet
  is tolerated only for the sections of the second half;
- every `yaml` block parses, and a `Workflow`, a `Policy` or a `KnowledgeBase` is validated by the
  platform's own models (`choregos_core.parse_workflow`, the policy model, the CLI's manifest model),
  with no warning;
- every `json` block parses;
- every `choregos` command in a `bash` block exists, with the options it is given;
- every screen path written as `**A › B**` names labels the console has, and every console address
  in backticks (`/p/<project>/settings`) is a page of the console;
- no page sets `slug` or `id`, and every page has a `title` and a `description`.

## Markdown that the site accepts

The site is built with Docusaurus 3, which reads every page as MDX:

- Outside code, never write a bare `<` or `{`. Put placeholders in backticks: `<project>`,
  `{project}`. The test refuses them in prose.
- Diagrams are Mermaid code blocks (`mermaid`). The site renders them; so does GitHub.
- Link to another page of this folder with its relative path and its `.md` extension:
  `[Work items](use/work-items.md)`. Link to a reference page the same way (`../usage.md`): the import
  rewrites it to an absolute GitHub link at the imported release tag.
- Images live next to the page that shows them, in this folder. An image elsewhere in the repository
  is rewritten to its raw GitHub address at the tag.
- No HTML, no JSX, no import statements.

## How it is published

The documentation site (VargaFoundation/docs) imports this folder at the **latest release tag** of
Choregos, on a schedule and on demand, and opens a pull request there. A change merged here reaches the
site with the next release. The site's "Edit this page" link opens the file here, on `main`.
