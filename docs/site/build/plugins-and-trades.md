---
title: Plugins, extensions and editions
description: Tell the core, a plugin, an extension and an edition apart, and know where the work of each trade lives.
sidebar_position: 6
---

# Plugins, extensions and editions

The core of Tranibor carries governed work for any trade, and names none. Everything that belongs to
one trade (software delivery, IT service management, documentation, HR, investment research) comes in
a **plugin**. There is one such unit, and no other: no "pack", no "module", no "trade kit".

**What you will be able to do:**

- tell the core, a plugin, an extension, an extension seam and an edition apart;
- decide whether what you build is a plugin with content only, or one that also needs an extension;
- know what moves out of the core for software delivery before 1.0.

## The words

| Term | What it is | Who ships it | How it gets in |
| :-- | :-- | :-- | :-- |
| **Core** | The platform: work items, workflows and their interpreter, gates, governed actions, decisions, agents and their runs, budgets, audit, and the five surfaces 1.0 freezes. It names no trade. | GRAAL.SYSTEMS, Apache 2.0 | The images and the chart |
| **Plugin** | The one unit a trade ships. Its **content** (`plugin.yaml`: agents, assistants, skills, templates with their workflows, evals, procedures, MCP servers) and, only when the trade needs code inside the platform, its **extension**. Both halves carry the plugin's name and version. | Anyone | Content: an organisation administrator installs it (**Plugins** in the sidebar). Extension: the operator installs it in the images. |
| **First-party plugin** | A plugin GRAAL.SYSTEMS publishes. Same formats, same entry point, same checks, same seams. It has no access another plugin lacks. | GRAAL.SYSTEMS | As any plugin |
| **Extension** | Python code loaded at start-up by the API and the workers, from the `choregos.plugins` entry point. It is the code half of a plugin, or code that is no trade at all (an edition, an in-house connector). | The plugin's or edition's publisher | A distribution installed in the images |
| **Extension seam** | A named interface of the core that an extension calls when it loads, for example `tranibor_adapters.register` (a connector type), `effects.declare_effect` (an effect of a governed action), `extensions.declare_router` (routes), `edition.declare`. | The core | Part of the core; frozen at 1.0 |
| **Edition** | Which platform runs: community or enterprise. An edition changes what the platform allows (several organisations, sign-in, isolation), never what work it does. | Community: the core. Enterprise: GRAAL.SYSTEMS, from a separate repository | The enterprise edition is an extension that calls `edition.declare` |

So: the **enterprise edition is not a plugin**. It uses the same entry point and seams as a plugin's
extension, but it brings no trade. And a **first-party plugin is not part of the core**: if the core
needed something only a first-party plugin could reach, that would be a missing seam, and the fix is
the seam.

## What goes in which half

Most plugins are content only. A plugin needs an extension only when its trade must run code inside
the platform's own processes.

| A plugin declares | In its content | In its extension |
| :-- | :-- | :-- |
| Agents, assistants, skills, evals | yes | |
| Templates, with their workflows and knowledge bases | yes | |
| Setup procedures for its connectors | yes | |
| Tools of an outside system | an MCP server (an image pinned by digest), governed tool by tool | |
| A connector type (a tracker, a directory, a market data feed) | an MCP server that implements the tracker profile | a connector type, through `tranibor_adapters.register` |
| Effects of a governed action | | `effects.declare_effect` |
| Its own tables, its own routes | | the `choregos.migrations` entry point, `extensions.declare_router` |
| Gates and the facts they read | | `gates.gate` and `gates.context_provider` ([ADR 0181](../../adr/0181-the-contracts-speak-no-trade.md), round C0) |
| Step effects for `does:` | | `step_effects.step_effect` (ADR 0181, round C0) |
| Inbound event types, finding types, memory kinds, stage roles | | `INBOUND_EVENT_TYPES`, `FINDING_TYPES`, `MEMORY_KINDS` of `tranibor_contracts`, `stage_roles.declare_stage_roles` (ADR 0181, round C0) |
| Durable code of its own (a release train) | | a worker seam (ADR 0181, round D) |

A deterministic job step runs an image the operator allows (`TRANIBOR_JOB_IMAGES`); a plugin's template
names it.

## One example per trade

- **Software delivery**: `software-delivery`, a first-party plugin with both
  halves. Its content is published today: the delivery agents (triage to the production go/no-go) and
  the `madr-4` skill. By 1.0 it also holds the repository templates, and its extension,
  `tranibor-software-delivery`, holds what the core carries today: the twelve gates that read a diff or
  a CI (`ci_green`, `diff_size_max`, `scope_respected` …) and the context provider that reads the diff
  and the checks for them, the step effects `open_pr` and `merge_pr`, the `scm.*`, `ci.*` and `cd.*`
  event types, the finding types `bug`, `perf`, `tech-debt` and `flaky-test`, the roles `fix_ci`,
  `address_review`, `release_notes` and `verify_prod`, and the release train.
- **IT service management**: `servicenow`. Content only: an ITSM triage agent, its skill, and an MCP
  server that implements the tracker profile, so a project's tickets live in ServiceNow and every write
  the agent makes is a governed operation of that server.
- **Documentation**: a plugin with content only: a drafting agent, a skill with the house style, a
  template whose workflow goes draft, review, publish. It uses the core's own gates (`outputs_present`,
  `output_schema`) and a person's review. It would need an extension only to add a gate of its own.
- **HR**: the joiners and leavers workflows (today the core's `joiners-leavers` template). Content
  only: two coordinator agents, two skills, two workflows whose every write to the directory, the device
  fleet, the carrier or the badge readers is a governed action on the organisation's connectors.
- **Investment research**: `investment-research`, a first-party plugin with both halves. Content: the
  campaign template, two agents, two skills, their evals and the setup procedures of its connectors.
  Extension: the `marketdata` connector family, the distribution `tranibor-investment-research` in
  `plugins/investment-research/` of the core's repository, which the published images install. Its
  template's deterministic steps run `choregos-quant` in the plugin's job image,
  `tranibor-investment-research`, which each release builds FROM its own runner image.
- **Procurement**: `procurement`, planned ([ADR 0198](../../adr/0198-procurement-is-a-plugin-whose-code-half-is-a-job-image.md),
  stream S84), a first-party plugin with a code half and no extension. Content: a template that compares
  the supplier offers a person hands a work item, two agents that never compute, their skills and
  evals, later an MCP server for the organisation's purchasing system. Code: the distribution
  `tranibor-procurement`, whose only product is the command its deterministic steps run, in the
  plugin's job image: it extracts the text of the files, computes every number a memo may quote, and
  registers nothing with the platform. It uses the core's own gates (`numbers_measured`,
  `evidence_facts`, `output_schema`).

## What changes before 1.0

The project lead decided on 2026-11-19 ([ADR 0181](../../adr/0181-the-contracts-speak-no-trade.md),
option D) that the core's contracts and frozen surfaces speak no trade, and that software delivery is a
first-party plugin like any other, in its own distribution, before 1.0.

| Today in the core | By 1.0 |
| :-- | :-- |
| The roles `developer` and `release_captain` | `contributor` and `approver`, in the core |
| `pr_url` on a work item and a step's result, `repository` fields | `links`, with `rel: change_request`; `workspace` |
| The twelve gates that read a diff or a CI | `software-delivery`'s extension, under the same names |
| `does: open_pr` and `merge_pr`, the `scm.*`, `ci.*`, `cd.*` events | `software-delivery`, under the same names |
| The release train, its routes and the DORA figures | `software-delivery` |
| The repository templates | `software-delivery`'s content |
| `packages/ontology` | removed |
| The `quant` command and the investment template | `investment-research`: done (S83-07) |
| The `marketdata` connectors | `investment-research`'s extension: done (S83-08) |

Gate names, event types and effect names do not change: stored workflows and subscribers keep working
once the plugin is installed. A renamed name is served beside its former one for a window of at least
two weeks, then only under the new one. One list, `tranibor_contracts.window`, names every former name
and the release that removes it: the roles, the fields and the role groups renamed in round C1 leave in
0.30.0. Until then a request may send either spelling, a response carries both fields, and an identity
provider may send `contributors` and `approvers` beside `developers` and `release-captains`.

## Go further

- [Package agents as a plugin](plugins.md): lay out, validate and install a plugin's content.
- [Writing a plugin](../../plugins.md), the reference of the format, and
  [Shipping a connector from outside this tree](../../development.md#shipping-a-connector-from-outside-this-tree)
  for an extension.
- [Concepts](../concepts.md#editions): the two editions.
