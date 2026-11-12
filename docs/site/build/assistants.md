---
title: Ship an assistant in a plugin
description: Write an assistant manifest, declare what it needs, remembers, knows and does, test it with conversation suites, and validate it with the CLI.
sidebar_position: 5
---

# Ship an assistant in a plugin

The billing team at `acme` wants people to ask questions about invoices in plain words. You ship that
as an **assistant**: a file of `kind: Assistant` in a [plugin](plugins.md). It wraps one agent of the
plugin and says what that agent needs to talk to a person: its tools and the identity each is called
under, what it remembers, what it knows, what it does in the background, where it appears and what it
may spend. An organisation then installs it for a group of people.

The core knows needs, operations, subjects and memory spaces. It never knows what an invoice, a client
or an instrument is: all of that is your plugin.

**What you will be able to do:** write and validate an assistant, declare needs and identities, memory
spaces, knowledge seeds, tasks, surfaces and a policy, and write conversation suites that gate its
releases.

**Before you start:**

- the [CLI](../cli/install.md); `choregos plugin validate` works offline;
- the plugin layout of [Package agents as a plugin](plugins.md) and an agent of runtime `prompt` or
  `hosted` ([Build an agent](agents.md)). An agent that needs a repository, as a code agent does, cannot
  be an assistant's.

## 1. A minimal assistant

Four files: the plugin manifest, an agent, the assistant, and a conversation suite.

```yaml title="billing-assistant/plugin.yaml"
apiVersion: choregos/v1
kind: Plugin
metadata:
  name: billing-assistant
  version: 0.1.0
  publisher: ACME billing team
  license: Apache-2.0
  description: An assistant that answers questions about the invoices of acme.
  core: ">=0.26.0"
contents:
  agents: [agents/invoice-helper.yaml]
  assistants: [assistants/billing-assistant.yaml]
```

The agent never names a connector of the organisation. It names a **need** (`invoices`) and calls
`invoices__get_invoice`. `{{ config.company }}` is read from the setting the installer gives.

```yaml title="billing-assistant/agents/invoice-helper.yaml"
catalogue:
  version: 1
  role: invoice-helper
  summary: Answers a person's questions about invoices, citing the invoice it read.
agent:
  slug: invoice-helper
  display_name: Invoice helper
  description: Explains the invoices of a company and never changes one.
  spec:
    runtime: { kind: prompt }
    instructions: |
      You help the people of {{ config.company }} with their invoices.
      Every amount comes from a tool call. Name the invoice number you read.
    mcp_servers:
      - { connector: invoices, tools: [get_invoice, list_invoices] }
```

```yaml title="billing-assistant/assistants/billing-assistant.yaml"
apiVersion: choregos/v1
kind: Assistant
metadata:
  name: billing-assistant
  display_name: Billing assistant
  description: Answers questions about the invoices of your company.
agent: invoice-helper
config_schema:
  type: object
  required: [company]
  properties:
    company: { type: string }
tools:
  - { need: invoices, operations: [get_invoice, list_invoices], access: read, identity: service }
evals: [evals/billing-assistant.yaml]
```

Only `metadata` (with `name`, `display_name`, `description`) and `agent` are required; every other key
is optional. The agent is one of this plugin's, and its `mcp_servers` may only name needs the assistant
declares. The suite is the file of step 8:

```yaml title="billing-assistant/evals/billing-assistant.yaml"
kind: conversation
agent: invoice-helper
description: The assistant reads an invoice before it answers, and never changes one.
threshold: 1
cases:
  - name: amount-comes-from-the-invoice
    turns: ["How much is invoice INV-42?"]
    tool_results:
      invoices__get_invoice: { number: INV-42, total: 120.0, currency: EUR }
    assert:
      must_call: [{ tool: invoices__get_invoice, before: any }]
      must_not_contain: [{ regex: "(?i)i cannot know" }]
    rubric:
      - { criterion: gives the total of the invoice and names its number, threshold: 0.8 }
```

```bash
choregos plugin validate billing-assistant/
```

**What you should see:** `✓ billing-assistant 0.1.0 sha256:…`.

The rest of this page adds one section of the manifest at a time. They come from the test plugin
`investment-assistant` of the repository (a personal investing assistant on fakes, which proves the
core stays free of business words). Each fragment goes under the top level of the assistant file.

## 2. Needs and identities

`tools` is a closed list of **needs**. An operation not listed is not callable. Each need says who the
call is made as:

| `identity` | The call is made as | Use it for |
| --- | --- | --- |
| `service` | the connector's own credential | public or shared data |
| `user_exchange` | the person: their token is exchanged for one the source accepts (RFC 8693) | a source that trusts the same identity provider |
| `user_consent` | the person, through an account they connected once | a source with its own OAuth |

```text
tools:
  - { need: news, operations: [search_news, get_article], access: read, identity: service }
  - { need: brokerage, operations: [list_positions], access: read, identity: user_consent, required: false }
  - { need: orders, operations: [place_order], access: write, identity: user_consent,
      approval: console, required: false }
```

- `required: false` lets the owners, then each person, switch the need off. A required need must be
  bound before anyone can converse.
- A **write** need must say `approval`: `inline` (the person confirms in the conversation) or `console`
  (in the console). `none` is refused, and a read need takes no approval. `inline` needs a person's
  identity, never `service`.
- A person's identity never falls back to the service's: without a token the call fails and the
  assistant says what is missing.
- The source decides what a person may read, not Choregos.

### Upgrading a plugin that changes a need

When a new version of your plugin changes `tools`, each installation follows it. A need you drop has its
binding switched off and kept for the audit. A need you add is unbound: the installation shows it as
something to do. A need whose operations, `access` or `identity` changed is checked again against the
connector it is bound to; if the connector still fits, nothing is asked of the owners, if not the binding is
switched off and the installation says why. Flipping `required` changes what the installation needs to be
ready. The owners are told by email when the installation has an email delivery. Keep a need's name when
you change it, since a new name is a new need.

## 3. Memory spaces

```text
memory:
  - name: investor_profile
    scope: user
    kind: profile
    writes: agent_proposes
    schema: { type: object, required: [risk_tolerance], properties: { risk_tolerance: { enum: [low, medium, high] } } }
  - name: instrument_notes
    scope: user_subject
    subject: { type: instrument, key: isin, resolver: none }
    kind: facts
    writes: agent_proposes
    max_facts: 50
```

- `scope`: `installation` (one for everyone, written by the owners only, so `writes: owner_only`),
  `user`, or `user_subject` (one per person and per subject).
- `kind`: `facts` (rows, each `proposed`, `active` or `rejected`) or `profile` (one JSON document that
  needs a `schema` and no `max_facts`).
- `writes`: `agent`, `agent_proposes` (the person confirms), `user_only` or `owner_only`.
- A `user_subject` space needs a `subject` of `{type, key}`. The subject opens only if a need marked
  `subject_resolver: true` returned it in that conversation, or if the subject says `resolver: none`
  (a public thing). The model never opens a subject by naming it.

## 4. Knowledge seeds

```text
knowledge:
  - { name: investment_policy, seed: knowledge/investment-policy, serving_mode: index, upstream: propose_updates }
```

The seed is a folder of your plugin with a `knowledge.yaml` (a `KnowledgeBase` manifest, as in
[Knowledge](../use/knowledge.md)) and a markdown corpus. Installing the assistant copies it into a
knowledge base of the organisation, which its owners then edit. `upstream` says what a plugin upgrade
does to the copy: `follow`, `propose_updates` or `frozen`. An upgrade never deletes a document, and a
document the owners changed is proposed, never overwritten.

## 5. Tasks

```text
tasks:
  - name: morning_brief
    workflow: workflows/morning-brief.yaml
    triggers: [{ kind: schedule, cron: "30 7 * * 1-5", user_timezone: true }]
    delegation: read_only
    needs: [news, market_data, brokerage]
    deliver: [inbox, email]
```

A task runs a workflow of the plugin, in the subscriber's personal space. A trigger is `on_demand`,
`schedule` (a cron in the person's time zone), `poll` (a cron that calls one read operation and starts
the workflow only when a condition on its result holds) or `trigger` (a system sends an event for one
person). `deliver` is `inbox`, `email`, `teams` or `webhook`.

```text
triggers: [{ kind: trigger, subscriber: /attendee/email, idempotency: /event/id }]
```

`subscriber` is the JSON pointer, in the event, of the email of the person it is for; it is matched against the
people subscribed to the task. An event that names nobody, several people, or nobody subscribed is refused and
journaled for the installation's manager, and the same event id never starts the task twice. The sender signs
the event with a key the manager reads on the installation.

A task that reads with a person's identity while they are away needs `delegation: read_only` and lists
the `needs` it reads; the person agrees to that exact list. A delegated task never calls a write need.
A task that reads only `service` needs takes `delegation: none`. Each task's workflow is validated like
any workflow.

## 6. Surfaces

```text
surfaces:
  console: true
  mcp:
    prompts:
      - name: explain_move
        description: Explain why an instrument moved today.
        arguments: [{ name: instrument, required: true }]
        message: "Why did {instrument} move today?"
  embed:
    context_schema: { type: object, properties: { instrument: { type: string } } }
```

`mcp.prompts` become prompts of the assistant's own connector, and every `{argument}` of a message must
be a declared argument. `embed.context_schema` is the shape of the context a host page may pass to the
[embedded assistant](../integrate/assistants.md#embed-an-assistant-in-your-application).

## 7. Policy

```text
audience:
  suggested_groups: [grp-investors]
  personal_data: true
policy:
  budget: { per_user_month_usd: 20, per_conversation_usd: 2, per_installation_month_usd: 500 }
  conversation: { retention_days: 30, max_turns: 60, idle_close_minutes: 240 }
  data: { classification: sensitive, erasure: [by_user] }
```

These are the defaults the installation may tighten, key by key. An assistant that declares
`personal_data: true` must also declare `policy.conversation.retention_days` and at least one `erasure`
mode (`by_user`, `by_subject`), or the plugin does not validate. `by_subject` lets auditors erase what
is remembered about one subject.

## 8. Conversation suites

A **conversation suite** is an eval file of `kind: conversation`, listed under `evals` in the assistant.
A case is a list of user `turns`, `tool_results` that stand for the sources (the tool names are
`need__operation`), and two kinds of check.

1. **Assertions** are exact and run first, on the journal of the conversation: `must_call` (with
   `before: any` or a tool), `must_not_call` (which can carry a `when` on the context), `call_order`,
   `must_cite` (a figure pattern and the source pattern that must follow), `must_not_contain` (text or
   `regex`) and `length`. A rule such as "never read the account before the quote" is a fact of the
   journal, not an opinion.
2. **The judge's rubric** comes next: a model scores each `criterion` against its `threshold`. A
   malformed or refused judgement fails the case.

The `threshold` of the suite (1 by default) is the share of cases that must pass every assertion. The suite counts in promotion: the
agent version an assistant wraps does not reach `production` below it. The platform plays it through the
model gateway; with no gateway, promotion is refused rather than let through (your operator's
[upgrade notes](../host/upgrades.md#upgrading-to-026) say how to opt out).

A thumb down from a person is proposed to the agent's managers as a pseudonymised candidate case. It
never enters a suite on its own.

## What `choregos plugin validate` refuses

Each refusal names the file and the path in it.

- An `agent` that is not in `contents.agents`, or whose runtime is not `prompt` or `hosted`.
- A `{{ config.key }}` in the instructions that `config_schema` does not declare.
- A tool the agent calls that no need declares, or an operation the need does not list.
- A write need with no `approval`, with `none`, or an `inline` approval under `service`; a read need with
  an approval.
- A `user_subject` memory without a subject, a `profile` without a `schema`, an `installation` space
  that is not `owner_only`; a subject resolver that is not a need marked `subject_resolver: true`.
- `personal_data: true` without a conversation retention and an erasure mode.
- A task that names a need or an operation the assistant does not declare, or a `read_only` task without
  its `needs`; a poll without `cron`, `operation` and `when`; a
  `trigger` without a `subscriber` pointer.
- A workflow, seed folder, or suite file that is not in the plugin, or a workflow that is not valid.
- A suite that evaluates another agent than the assistant's, repeats a case name, or names a tool that
  is not a `need__operation` of the assistant.
- A prompt message that uses an argument the prompt does not declare.
- A name that is not `snake_case` (needs, operations, memory spaces, knowledge bases, tasks, prompts,
  subject types and keys).

## Go further

- Install it and bind its needs: sidebar **Assistants**, then **Manage installations** (organisation
  administrators). The full contract is in the [plugins reference](../../plugins.md#assistants).
- [Use an assistant](../use/assistants.md), as the people you ship it to see it.
- [Connect it to claude.ai or embed it](../integrate/assistants.md).
- The decisions: [ADR 0133](../../adr/0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)
  and [ADR 0140](../../adr/0140-conversation-evals-and-user-feedback.md).
