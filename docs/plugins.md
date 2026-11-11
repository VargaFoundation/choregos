# Writing a plugin

A plugin packages agents and what they need, in one format the platform understands
([ADR 0093](adr/0093-an-agent-ships-as-a-plugin.md)). This page covers writing one and checking it
locally with the CLI, how a deployment declares the sources it fetches plugins from and how it trusts
them, what installing one into an organisation does, and the plugins a deployment installs by default.

"Plugin" here means this package. A Python wheel loaded through the `choregos.plugins` entry point is an
**extension**.

## The layout

```
hello/
├── plugin.yaml                      # the manifest (plugin.schema.json)
├── agents/greeter.yaml              # a catalogue entry: `catalogue:` + `agent:`
├── skills/greeting/SKILL.md         # a skill directory
├── templates/hello-flow/manifest.yaml   # a template, with its files
├── evals/greeter.yaml               # an eval suite naming one of the plugin's agents
├── evals/matrix/greeting.yaml       # a matrix case of the core's eval matrix (`matrix_cases`, #1372)
├── procedures/connector.mcp.hello.md    # a procedure (ADR 0067 front matter)
└── servers/hello.yaml               # an MCP server the platform runs (plugin-server.schema.json)
```

A complete example lives in `tests/fixtures/plugins/hello/`; `tests/fixtures/plugins/servicenow/` ships
matrix cases and a server with a `fake`.

**A plugin is what its manifest declares**: `plugin.yaml`, a `README.md` and a `LICENSE` at its root when
present, and exactly the paths `contents` names — each file, every file under a skill or template
directory, every file a procedure pattern matches, and what an assistant names (its knowledge seeds, task
workflows and eval suites, [Assistants](#assistants)). Anything else in the directory (a server's source, its
`.venv`, build output) is not read, not validated, not digested and not packed, so a server's code can
live beside its plugin without shipping in it.

```yaml
apiVersion: choregos/v1
kind: Plugin
metadata:
  name: hello                 # [a-z0-9-], unique within a source
  version: 1.0.0              # semver; a published version is immutable
  publisher: Varga Foundation
  license: Apache-2.0
  description: A test plugin that ships one of everything a plugin may ship.
  homepage: https://github.com/VargaFoundation/choregos
  core: ">=0.20.0"            # the core versions it was validated against
requires:
  plugins: []                 # [{name, version: "^1"}]
  connectors: {}              # project connectors, as in a template
contents:
  agents: [agents/greeter.yaml]
  skills: [skills/greeting]
  templates: [templates/hello-flow]
  evals: [evals/greeter.yaml]
  procedures: ["procedures/*.md"]   # the only list that may use *
  servers: [servers/hello.yaml]
  # matrix_cases: [evals/matrix/greeting.yaml]   # agent cases of the core's eval matrix (#1372)
  # assistants: [assistants/helper.yaml]        # assistants wrapping an agent of the plugin (ADR 0133)
```

## What is checked

`choregos plugin validate` reports every problem it finds, each with its file:

- **Paths stay inside the plugin.** A path cannot be absolute or hold a `.` or `..` segment, and no
  declared path, nor anything under a declared directory, may be or pass through a symbolic link, even
  one pointing inside the plugin. These are the template rules, shared in `choregos_core.paths`. A link
  elsewhere in the directory is ignored with the rest of what is not declared.
- **The manifest** matches `plugin.schema.json`. A kind other than `Plugin`, a version that is not
  semver, or an unknown key is refused.
- **Agents** have the shape of a built-in catalogue entry (ADR 0040): `catalogue.version`, `role` and
  `summary`; `agent.slug`, `display_name` and `spec`. The full `AgentCreate` validation runs when the
  plugin is installed.
- **Skills** are directories that hold a `SKILL.md`.
- **Templates** have a `manifest.yaml` that matches `template.schema.json`. A template whose project
  tracker is a server of the plugin says `requires.connectors.tracker: plugin:<plugin>/<server>`; the
  server must be one of the same plugin and declare `implements: [tracker]` (see
  [The tracker profile](#the-tracker-profile)).
  A template may ship the knowledge bases its agents read (`defaults.knowledge`, see
  [Knowledge bases](#knowledge-bases)); a source that reads a template input must name an input the
  template declares.
- **Evals** match `plugin-evals.schema.json` and name an agent of the plugin (see [Evals](#evals)).
- **Matrix cases** (`contents.matrix_cases`) are agent cases of the core's eval matrix, in the format
  of the core's own (see [Matrix cases](#matrix-cases)): a mapping with a `task`, an `agent` of the
  plugin (or the case's own `agent_spec`), `tools_cells` among `mcp` and `scripts`, and every connector
  `plugin:<plugin>/<server>` naming this plugin and one of its servers, under that server's name; no two
  cases of one file name.
- **Procedures** go through the same parser as the core's (ADR 0067): front matter, fixed sections,
  links listed in `vendor_docs`, no secret-shaped string. A procedure gives the address of its server's
  webhook with `{{plugin_webhook_url:<plugin>/<server>}}`, filled with
  `<api>/api/v1/webhooks/plugins/<org>/<plugin>/<server>`; it may name only a server of its own plugin that
  declares a `webhook`.
- **Servers** match `plugin-server.schema.json`:
  - the image is pinned by `@sha256:`;
  - every operation states its `access` (`read` or `write`);
  - a read has no `compensation`, and a compensation names an operation of the same server;
  - `paths` (`mcp`, `health`; defaults `/mcp` and `/healthz`) name where the server answers;
  - `platform_arguments` (`action_key`, `work_item_key`) are arguments the platform fills, never the
    agent;
  - operation names are unique;
  - an egress `from_fields` entry is a field of `config_schema`;
  - a server that `implements: [tracker]` declares every tool of the
    [tracker profile](#the-tracker-profile), each with the profile's access;
  - a server's `fake` ([On its fake](#on-its-fake-for-the-eval-matrix)) switches it with variables
    that are neither `CHOREGOS_*` nor any its configuration reaches.

  An operation's `human_approval` is a floor: an organisation may raise it, never lower it. A tool the
  server serves but does not list is refused.

Validation also **warns**, without refusing: an agent that reads a knowledge base (`spec.knowledge`) that
no template of the plugin ships runs only in an organisation that already has that base and serves it to
the project — its runs stop otherwise. `choregos plugin validate` prints each warning after the digest
(`! agent `x` reads the knowledge base `y`, which no template of this plugin ships …`).

### Knowledge bases

An agent names the bases it reads by slug, and its run stops when a base does not exist or is not served
to its project. A plugin ships the base with the template that brings the agent
([ADR 0117](adr/0117-a-template-ships-the-knowledge-bases-its-agents-read.md)), in the shape of a
`kind: KnowledgeBase` manifest less what the project decides (scope, uploads, eval set):

```yaml
# templates/hello-flow/manifest.yaml
defaults:
  workflow: template:default-simple@1
  knowledge:
    - slug: greeting-docs              # what the agents name in spec.knowledge
      name: Greeting documentation
      serving_mode: files              # or index
      sources:
        - name: Repository docs
          kind: git                    # git only
          config:
            url_from_input: repo       # or url: https://github.com/acme/handbook
            ref: main
            paths: ["docs/**/*.md"]
          interval_minutes: 60         # or refresh_mode: cron, refresh_cron, refresh_timezone
inputs:
  - { name: repo, type: github-repo }
```

A project born of the template gets each base owned by it and served to it, its sources attached and
synced at once. A `github-repo` input left empty is the project's repository, read through the
deployment's GitHub App with a read-only token; another private repository names an organisation
`documents/git` connector (`connector:`). A base of that slug the organisation already has is reused when it
is served to the project and never rewritten; otherwise the project is born without it and the audit log
says why (`template.knowledge.skip`). [Knowledge bases from a template](usage.md#knowledge-bases-from-a-template)
has the full rules.

### Evals

An eval file (`plugin-evals.schema.json`) has the shape of the catalogue's cases (the core's own until
S43-09, now the `evals/` of the built-in plugins), plus the API's `AgentEvalCase` list:

```yaml
agent: greeter                     # an agent of this plugin
description: The greeting names the author.
must_contain: [greet the author]   # level 1: in the rendered instructions, case-insensitive
must_not_contain: [without approval]
min_score: 1.0                     # optional, the share of must_contain to find
cases:                             # optional, AgentEvalCase
  - name: names-the-author
    input: {title: Say hello, body: Greet Ada.}
    must_contain: [Greet the author]   # case-sensitive, on the instructions rendered on `input`
  - name: names-the-reporter
    input:
      title: Say hello
      fields:                      # optional, rendered as `ticket.fields` (S51-10)
        tracker: {kind: jira, key: FON-270, reporter: {display_name: Ada Lovelace}}
    must_contain: [Ada Lovelace]   # when the instructions say {{ ticket.fields.tracker.reporter.display_name }}
  - name: live-greeting
    live: true                     # a capped trial on a real model (S40-02d)
    expect_status: done
    max_cost_usd: 0.25
    must_contain: [Hello]
```

An unknown key is refused, and a suite holds `must_contain`, `cases` or both. A case's `input` is the
API's `AgentSampleInput`: `title`, `body` and `fields`, the work item's fields the instructions see as
`ticket.fields` (`{}` when absent), at most 64 KiB of JSON. `fields.tracker` plays a ticket filed from a
tracker (ADR 0118); a plugin's case may set it, a caller's work item may not. The installer plays level 1
and every case that is not `live`; a failure refuses the plugin. A `live` case is validated, not run at
install.

### Matrix cases

A matrix case is an agent case of the core's eval matrix (S57-09; [the eval matrix
runbook](runbooks/live-eval-matrix.md#a-plugins-cases-1372)), in the same format as the core's own
(`choregos_playbooks/evals/agent_cases/*.yaml`): its agent, its task, its doors (`tools_cells`), its
connectors, its evidence, its scripted script and answers, its assertions. The matrix names it
`plugin:<plugin>/<file stem>` (`plugin:servicenow/itsm-triage-similar-incidents`). A connector may be
one of the plugin's servers:

```yaml
connectors:
  servicenow: { kind: "plugin:servicenow/servicenow", scene: default }   # named after the server
```

The installer keeps matrix cases and never plays them: the matrix does, in a scripted cell against the
server's fake (in the core's CI, a fake registered for the server) and in a live cell against the
server's eval instance.

A case judges the task, not the door (S57-11b, [What a case checks of its
run](runbooks/live-eval-matrix.md#what-a-case-checks-of-its-run)): its facts are the answer's, its
guarantees the run's (actions proposed and executed, tools never called, security events). What a door
costs — `calls_by_origin`, `max_model_requests` — is a measure, written under `measures`; a case that
still writes it under `assertions` has it read as a measure, never as a pass criterion.

### Assistants

An assistant ([ADR 0133](adr/0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md))
is a file of `kind: Assistant` listed under `contents.assistants` and validated by `assistant.schema.json`.
It wraps one of the plugin's agents and declares what the agent needs to converse with a person: the
configuration the installer sets, the audience, the tools it needs and the identity each is called under,
its memory spaces, the knowledge it offers, its background tasks, its surfaces and its policy.

In a conversation, the agent's `mcp_servers` name needs: it sees `<need>__<operation>` for the read tools of
the connector the installation bound to each enabled need, never the connector's name. The core carries no
business vocabulary: `test_assistant_genericity.py` runs the fixture below end to end on fakes and
`test_core_names_no_business_vocabulary.py` fails when an identifier of the core names its domain.

```yaml
# assistants/investment-assistant.yaml (tests/fixtures/plugins/investment-assistant)
apiVersion: choregos/v1
kind: Assistant
metadata: { name: investment-assistant, display_name: Investment assistant, description: "…" }
agent: investment-analyst            # an agent of this plugin, runtime prompt or hosted
config_schema: { type: object, required: [house_name], properties: { house_name: { type: string } } }
audience: { personal_data: true }
tools:
  - { need: market_data, operations: [get_quote], access: read, identity: service, subject_resolver: true }
  - { need: orders, operations: [place_order], access: write, identity: user_consent, approval: console }
memory:
  - { name: instrument_notes, scope: user_subject, subject: { type: instrument, key: isin, resolver: none },
      kind: facts, writes: agent_proposes }
knowledge:
  - { name: investment_policy, seed: knowledge/investment-policy }
tasks:
  - { name: morning_brief, workflow: workflows/morning-brief.yaml, delegation: read_only,
      needs: [market_data], triggers: [{ kind: schedule, cron: "30 7 * * 1-5", user_timezone: true }] }
policy:
  conversation: { retention_days: 30 }
  data: { classification: sensitive, erasure: [by_user] }
```

`choregos plugin validate` refuses, naming the file and the JSON path of the field:

1. an `agent` that is not an agent of the plugin, or whose runtime is not `prompt` or `hosted`;
2. a `{{ config.<key> }}` of the agent's instructions that `config_schema` does not declare;
3. a connector of the agent's `mcp_servers` that no need declares, or a tool that is not an operation of
   its need (patterns included: an assistant's tools are a closed list; the agent calls them
   `<need>__<operation>`);
4. a write need without `approval`, or with `approval: none`; an `inline` approval under the `service`
   identity;
5. a `user_subject` memory without `subject`, or without a resolver (`resolver: none` for a public subject,
   a need marked `subject_resolver: true` otherwise);
6. `audience.personal_data: true` without `policy.conversation.retention_days` and an `erasure` mode;
7. a task that calls a write need, or that reads with the person's identity while it runs without them (a
   schedule, a poll) and has no `delegation: read_only`; a delegated task lists the needs it reads
   (`needs`), which the person sees when they agree;
8. a name (`need`, operations, `memory`, `knowledge`, `tasks`, prompts) that is not English `snake_case`
   (a double underscore separates a need from its operation and is refused in a name).

Names are unique within their list, a prompt's `{argument}` placeholders are its arguments, and every file
the assistant names ships with the plugin: each knowledge seed (a directory holding a `knowledge.yaml` in
the `kind: KnowledgeBase` shape, and its `corpus/`), each task workflow and each eval suite is a declared
file, packed and digested like a template's. Conversing with an assistant comes with the other stories of
stream S63 ([the plan](plan/PLAN-2026-11-10-assistants.md)).

#### Tasks

A task of an assistant ([ADR 0137](adr/0137-a-standing-read-only-delegation.md) points 1, 2 and 5, S63-13) is a
workflow of the plugin run in the SUBSCRIBER's personal space: one work item per run (`tracker: internal`, the
subscriber as initiator, `documents.task` naming the task and the installation), the workflow published in the
space from the plugin's file when it is not there at that text. A person in the audience subscribes with
`PUT /me/assistants/{org}/{slug}/tasks/{task}` (`kind` one of the task's triggers, `timezone` an IANA name,
`cron` only for a schedule declared `user_timezone`), reads it with `GET`, leaves with `DELETE`, and starts it
now with `POST …/run`. Nothing changes in the Temporal workflows: the scheduler is a job of the API
(`assistant_tasks.task_loop`, each minute) that opens ordinary work items.

- **`schedule`**: the cron is read in the subscriber's time zone (07:30 in Paris and in Bogota are two windows).
  The window, the last fire minute within an hour of the clock, is claimed by one conditional update and keys
  the work item, so a window runs once. A new subscription never runs a window that passed before it.
- **`poll`**: on its window the declared read operation is called through the broker under the connector's own
  identity; `when` is a map of JSON pointers (`*` for every item of an array) to a condition (`not_empty`,
  `abs_gte`, `gte`, `lte`, `eq`). When `idempotency` runs through a `*` (`/events/*/id`) each item is an object,
  `when` is tested on each, and one run starts per object not seen before; the keys seen are kept on the
  subscription and in the work item's key.
- **`on_demand`**: the caller's click, one work item each, no delegation (the session's identity). A
  conversation of the installation is offered the same through ONE tool, `run_task(task)`, whose `task` is one of
  the manifest's on-demand tasks (`conversation_tasks`): it starts the task as the conversation's owner, with
  no delegation, under the click's budget and readiness checks, and tells the agent the work item's key (proved
  through a full model turn). The **`trigger`** kind (an external event starting a run) stays for later: ADR 0137
  point 2 defers it, so this is a deferral the ADR already made, not a departure.
- **Delegation**: a task with `delegation: read_only` can be subscribed, but its scheduled and polled runs are
  refused, the reason in `last_refusal`, unless the subscriber's standing delegation holds
  ([security](security.md#the-standing-delegation-adr-0137-points-3-4)). The steps of the run's workflow read
  under it too: a step's read of a connector the installation binds to a need is made as the person the need's
  identity says and judged again at each call, a write of such a step is refused (`write_under_delegation`).
  The console's task card shows what the delegation covers and until when, agrees to the exact list the API
  shows, and revokes. A run is also refused when the subscriber leaves the audience or when the subscriber's
  month, then the installation's, is spent.
- **Spend**: the ledger lines of a task run (model and tool) name the installation and the subscriber
  (`costs.task_attribution`), so the month caps above read them like a conversation's.
- **Reads journal**: every read of a task run's steps is a line with the work item's key (and the delegation's id
  under one), never the content.
- **Delivery**: the run's artifacts are listed by `GET /me/inbox` (always). `email` and `teams` go through a
  notifier, `webhook` is an HMAC-signed POST (`X-Choregos-Signature: t=…,v1=…`, as the A2A push notifications)
  to the installation's `policy_overrides.delivery.webhook.url`; each is sent once per artifact revision and a
  channel nobody configured is recorded as `not_configured` in the work item. No Teams adapter ships: the
  `teams` channel works through whichever notifier the installation's `policy_overrides.delivery.teams`
  (`{type, config}`) names. The webhook's signing key is shown ONCE to whoever manages the installation:
  `POST /orgs/{org}/assistants/{slug}/webhook-secret` (`assistant:manage`) returns it, a second call is 409
  unless `{"rotate": true}` makes a new generation (the console's installation page has the two buttons); each
  showing is audited, never the key.

#### Knowledge in a conversation

A conversation of an installation is offered `knowledge_search(base, query, top_k)` and
`knowledge_get(base, document_id)` next to its need tools (S63-05,
[ADR 0138](adr/0138-the-knowledge-an-assistant-offers.md) point 4), served in the
conversation's tool loop. The bases offered are those with `knowledge_bases.installation_id` set to the
installation (the copy of a seed made at install, S63-06), whatever the person's groups:
giving the assistant is the control, which is the whole decision about a `restricted` base or one scoped to
groups (no grant is read at conversation time: the owners' grants decide who edits a base, not whom the
assistant answers). An `index` base is searched; a `files` base is read with `knowledge_list(base?)` (id,
title, path, version of each ready document) and `knowledge_get`. A base outside the installation is neither offered nor reachable by
name, even when the person reads it through the knowledge routes, and a `knowledge_get` of another base's
document is refused. Each passage is framed as data with its document id, title, path and version
(`source_version`, else the content digest), and the agent's instructions tell it to cite
`[document, version]`.

#### Offered knowledge: a copy, an origin, proposed updates

Installing an assistant copies each seed it names (`knowledge: [{name, seed, serving_mode, upstream}]`,
S63-06, [ADR 0138](adr/0138-the-knowledge-an-assistant-offers.md) points 1-3) into an organisation base
`<installation slug>-<name>`, linked to the installation and read by its owner groups (a `group` grant with
the `owner` role; the base is `restricted`). Each markdown file of the seed's `corpus/` becomes a document,
indexed when `serving_mode` is `index`, marked `origin: seed@<plugin version>` and keeping the digest of the
seed file (`seed_digest`). An owner who uploads a file of the same name through the knowledge routes edits
that document, which becomes `origin: local`; a new file is `local` too.

Upgrading the plugin compares, per document, the seed the copy came from, the new seed and the copy:

| Document | `follow` | `propose_updates` | `frozen` |
| --- | --- | --- | --- |
| unchanged upstream | nothing | nothing | nothing |
| changed upstream, unchanged locally | updated | proposal | nothing |
| changed upstream and locally | proposal with its diff | proposal with its diff | proposal with its diff |
| new upstream | added | proposal | nothing |
| removed upstream | proposal to remove | proposal to remove | nothing if unchanged locally, else proposal |

A document is never deleted by an upgrade: removal is always a proposal. The owners (`assistant:manage`)
read `GET /orgs/{org}/assistants/{slug}/knowledge/updates[?status=]` and decide
`POST .../knowledge/updates/{update_id}` with `{decision: accept | refuse | merge, content?}`: `accept`
applies the seed's change, `merge` keeps their own `content` (a `local` document), `refuse` leaves the
document and is remembered (the same upstream state is not proposed again). The index follows each change.

Four more rules keep the owners' decisions:

- **A deleted document stays deleted.** An owner who deletes a document that came from a seed leaves a
  tombstone (`knowledge_seed_tombstones`): neither `follow` nor a proposal adds it back at the next upgrade. A
  document the owners wrote is not remembered.
- **A source makes a document local.** A source synced into the base (`knowledge sources`) that brings a file
  named like a seed document rewrites that document, which becomes `origin: local` and belongs to the source.
- **The seed's sources are attached at install.** The `sources` list of the seed's `knowledge.yaml`
  (`{kind: web | git | sharepoint | confluence, name, config, connector?, interval_minutes?}`) is attached to the
  base, `refresh_mode: manual`, and never synced there: opening the hosts in the egress allowlist and starting
  the sync stay the owners' gestures on the source routes, so installing never reaches out.
- **The owners are told.** When an upgrade opens new proposals, each owner (a member of the organisation in
  one of the owner groups) receives one message through the installation's `email` notifier (the one of its
  task deliveries, `policy_overrides.delivery.email`); nothing configured, nothing sent, and the proposals stay
  listed. A proposal already open is refreshed, not announced again.

#### Installing an assistant and binding its needs

Installing the plugin makes its assistants available; installing an assistant (S63-02) makes one of them an
object of the organisation, for an audience. The manifest is never copied: it is read again from the
installed plugin, so a plugin upgrade is seen at once.

```
POST  /api/v1/orgs/{org}/assistants      {"plugin": "investment-assistant", "assistant": "investment-assistant",
                                          "slug": "invest", "config": {"house_name": "Varga"},
                                          "owner_groups": ["investment-owners"], "audience_groups": ["grp-investors"]}
GET   /api/v1/orgs/{org}/assistants                         # those you manage
GET, PATCH, DELETE /api/v1/orgs/{org}/assistants/{slug}
GET   /api/v1/orgs/{org}/assistants/{slug}/readiness        # what is missing, and who may fix it
PUT   /api/v1/orgs/{org}/assistants/{slug}/bindings/{need}  {"connector": "news-feed"}
```

An installation is `incomplete` until its readiness has no item: each **required** need is bound to a
connector of the organisation that serves every operation of the need (as discovered, with the same
`access`) and can carry the need's identity mode, and each required key of `config_schema` has a value.
`service` is always supported; `user_exchange` and `user_consent` only by a connector whose config declares
them in `identity_modes`. A binding to a connector that cannot satisfy the need is a 422 naming why, and
nothing changes. An optional need may stay unbound or be switched off (`"enabled": false`). The status is
`suspended` while the plugin is uninstalled (installing it again resumes the assistant). Installing,
uninstalling and changing the owners are the organisation administrator's; configuring and binding are also
open to a member of an owner group (the identity provider's groups).

#### Conversation evals

An assistant's `evals` name conversation suites ([ADR 0140](adr/0140-conversation-evals-and-user-feedback.md),
`conversation-evals.schema.json`). A case is a scenario of user `turns`, the tool results the sources' fakes
answer (`tool_results`, by `<need>__<operation>`: a value answers every call, an array answers successive
calls), and exact assertions on the conversation's journal, the ordered tool calls and the final texts:

```yaml
kind: conversation
agent: investment-analyst
threshold: 1                      # the share of cases that must pass; below it, no production
cases:
  - name: quote-is-read-first-and-cited
    context: { relation: self }
    turns: ["What is the price of ACME today?"]
    tool_results:
      market_data__get_quote: { isin: FR0000000001, price: 12.34, as_of: "2026-11-10" }
    assert:
      must_call: [{ tool: market_data__get_quote, before: any }]   # the first call of the conversation
      call_order: [[brokerage__list_positions, market_data__get_quote]]
      must_not_call: [{ tool: orders__place_order, when: { context: { relation: coachee } } }]
      must_cite: [{ figure: '\d+(?:\.\d+)? ?EUR', source: '\(source: [a-z_ ]+, \d{4}-\d{2}-\d{2}\)' }]
      must_not_contain: ["you should buy", { regex: "(?i)guaranteed return" }]
      length: { min_words: 3, max_words: 120 }
    rubric: [{ criterion: explains without recommending a purchase, threshold: 0.8 }]   # lot 2: stored only
```

`choregos plugin validate` holds the file to the schema and refuses a suite for another agent than the
assistant's, a duplicate case name, and a tool (in an assertion, a condition or `tool_results`) that is not
a `<need>__<operation>` of the assistant's tools. `choregos_core.conversation_evals` evaluates a journal
(`evaluate_case`, `evaluate_suite`) and plays a case through the prompt loop on a scripted model
(`play_case`, `play_suite`). A release to production of an agent version an assistant wraps carries the
required check `conversation_suite`. A thumb down on an answer (`POST /orgs/{org}/agent-sessions/{id}/feedback`)
proposes a pseudonymised candidate case to the agent's managers (`GET /orgs/{org}/agents/{slug}/feedback-candidates`);
it never enters a suite by itself: an owner writes its assertions and accepts it
(`POST …/feedback-candidates/{id}/accept`, a stored addition to the agent's suites, validated like a suite) or
dismisses it (`…/dismiss`); both are final, and both are on the installation page of the console.

The suites of an installed assistant reach the gate: the installation registers its manifest's eval files, read
from the installed plugin, plus the accepted cases (`services/installed_suites.py`). A `SuitePlayer` plays them
(`set_suite_player`: tests and CI play on a scripted model, `scripted_player`; a deployment registers one on its
model gateway). Production is then refused below a suite's threshold, whether the revision is released
explicitly (409) or saved with `on_publish: stable` (production stays where it is). With no player registered
nothing can evaluate the suites and the gate adds no check. The judge that scores the `rubric` is not delivered
yet.

A task's workflow (`tasks[].workflow`) must be a valid workflow (the workflow schema and its static rules):
`choregos plugin validate` refuses a stub, naming the file.
#### Writes in a conversation

A write need with `approval: inline` is offered to the agent as `<need>__<operation>` (the agent's `mcp_servers`
name the need), but calling it executes nothing
([ADR 0139](adr/0139-an-assistant-through-the-mcp-door-and-an-embedded-component.md) point 5): it stores a
pending action (`conversation_actions`: operation, arguments, a readable summary, `proposed`) and answers the
agent that the action awaits the person. The conversation's owner decides, and only they:
`GET /me/conversations/{id}/actions`, `POST .../actions/{action_id}/confirm` (the broker calls the source with a
token exchanged for that person, `user_exchange`; the source stays the judge of the right to write; the result is
kept, a second confirmation is 409 `action_executed`) and `POST .../refuse`. A confirmed call is booked like a
tool call of a turn: a ledger line naming the installation and the person, the conversation's tool-call count and a
`tool.called` event (`as: person:<subject>`). When the need also serves a read operation `<operation>_preview`, the
proposal calls it as the person with the same arguments and keeps what it returned in `diff`. A proposal nobody
decides in 24 hours expires. The MCP door lists them as `pending_actions[{id, summary, confirm_url}]` and never executes
([ADR 0030](adr/0030-an-mcp-door-for-external-clients.md)). Owners may bind a write with
`approval: console` (above the manifest's floor), never below it (422). A `console` need bound with the
connector's own identity (`service`) is offered too: calling it proposes a GOVERNED action
([ADR 0035](adr/0035-governed-actions-in-the-core.md)) in the person's personal space,
decided at `/p/<space>/actions/<id>` with a fresh sign-in by whoever the project's rules name, played by the
`ActionWorkflow` under the connector's identity; the conversation's row is `governed` (`result.action` names the
action) and follows its outcome (`executed`, or `refused` when rejected); confirming or refusing it from the
conversation is 409 `governed`. A `console` need with a person's identity (`user_exchange`) is NOT offered: the
governed action's effect has no per-person identity, so it would write as the service; a proposal made before the
owners raised the approval is refused at confirmation with `approval_console`. A need bound `user_consent` with
`approval: inline` is offered and executed like a `user_exchange` one, but under the person's connected account (the
source receives the account's access token); without a connected account the confirmation is refused
`account_not_connected` and nothing reaches the source.

### Who may use an assistant, and what it may spend

Three permissions (S63-15), decided in one place (`services/assistant_access.py`) from the groups the
identity provider gave the person (`Principal.groups`, read from the user's row at each request):

| Permission | Held by |
| --- | --- |
| `assistant:manage` | the organisation administrator, and a member of an owner group of the installation |
| `assistant:use` | a member of an audience group of the installation; no organisation role carries it (an administrator outside the audience does not talk to the assistant) |
| `assistant:audit` | the owners, the organisation administrator, and the privacy officers: members of the groups in `CHOREGOS_ASSISTANT_PRIVACY_OFFICER_GROUPS` (`privacy_officer` is not a `Role`: the roles are a published contract) |

```text
GET  /api/v1/me/assistants                                    # the installations whose audience holds me
POST /api/v1/me/assistants/{org}/{slug}/conversations         {"context": {...}}   # optional context
```

Opening puts a conversation in the caller's personal space. The `context` is checked against the manifest's
`surfaces.embed.context_schema` (422 otherwise, and 422 when the manifest declares none) and reaches the agent
as data in a system message, never as a resolved subject. An installation that is not `ready` answers 409 with
its `readiness`; a caller outside the audience gets 404, as for an assistant that does not exist.

The audience is evaluated again at every turn (`POST /me/conversations/{id}/messages`): a person who left the
group is answered 404 before the model is called, and nothing is deleted, so returning to the group resumes
the conversation. Three caps, from the manifest's `policy.budget` overridden key by key by the installation's
`policy_overrides.budget`, bound each turn's key reservation: `per_conversation_usd`, `per_user_month_usd` (the
person's spend on this installation since the first of the month, UTC) and `per_installation_month_usd`. A
turn that has less than the floor left under any of them is a 409 before the model; the two month caps also
subtract what the turns in flight reserved, under a lock on the installation, so two turns racing one cap cannot
both be admitted. The month sums read the
ledger lines, which carry the installation and the person, so deleting a conversation does not give its spend
back.
#### Memory spaces of an assistant

The `memory:` of an assistant ([ADR 0136](adr/0136-memory-spaces-of-a-person-and-a-subject.md), S63-09)
declares its spaces: `installation` (one per installation, written by its owners only), `user` (one per
person) and `user_subject` (one per person and subject). A store is created at its first write, from the
manifest read again from the installed plugin, never by hand; a `user` or `user_subject` store (and its facts
and profile versions) is read by its person alone, in the application and, on PostgreSQL, by a restrictive
row-level policy.

- **Subject.** A `user_subject` space opens in a conversation only for a subject (`{type, key}`) that a tool
  of a need marked `subject_resolver: true` returned **in that conversation**: every `subject` object found
  at the top level of the tool's `structuredContent`, or in a top-level list of objects (`relations[].subject`),
  is recorded in the conversation's `resolved_subjects`. Any other subject is refused with
  `subject_not_resolved`. A space that declares `resolver: none` accepts any well-formed key.
- **Writes.** `agent` writes an active fact, `agent_proposes` a `proposed` one (the person confirms it on the
  memory page), `user_only` refuses the agent, `owner_only` is never written by a conversation (the agent's
  proposal waits, `proposed`). `max_facts` and `retention_days` bound a space: the oldest unpinned facts
  expire first. A `profile_patch` is merged into the active document and validated against the space's schema
  (`schema_violation`). The schema, the rule and the bounds are read again from the installed manifest at each
  write: after a plugin upgrade the next version of a profile is validated against the new schema, the
  versions already written are kept.
- **Tools.** The agent of an assistant's conversation is offered `memory_search(space, query, subject?)`,
  `memory_get_profile(space)` and `memory_propose(space, subject?, content | profile_patch, reason)`. A refusal
  is typed JSON (`{"error": "subject_not_resolved", ...}`). A project policy with `memory.enabled: false` (that
  of the person's personal space) turns every memory tool and the injection off.
- **Injection.** The first turn of a conversation carries the active `user` profile and a few facts of the
  `installation` and `user` spaces, framed as data, each space cut to its `inject_tokens` (300 by default).
  The facts are ranked, deterministically: the BM25 ranking of the opening context's text (a hint, never a
  subject: it opens no `user_subject` space) fused with recency, a pinned fact above the rest, the id breaking
  ties. `memory_search` ranks the same way.
- **The page.** `GET, PATCH, DELETE /api/v1/me/assistants/{org}/{slug}/memory/{space}` and `.../{space}/{subject}`
  list, confirm or reject a proposal, correct, pin, delete and `add` (a fact's `content` or a profile
  `document`, active at once: how a `user_only` space is written); they are the caller's own memory only.
  `GET /api/v1/me/assistants/{org}/{slug}/memory-export` returns all of it as JSON (every `user` and
  `user_subject` space, facts and profile versions) for the person to keep or carry elsewhere.
- **The owners' page.** `GET, PATCH /api/v1/orgs/{org}/assistants/{slug}/memory/{space}` (`assistant:manage`)
  show an `installation` space with its proposals and let the owners `confirm` or `reject` a proposed fact
  (`fact_id`) or profile version (`version`), or `add` to it themselves; a person's own space is 404 there.

### Data protection

- **Retention.** A daily task of the API deletes the conversations past `retention_until` with their events,
  the unpinned memory facts older than their space's `retention_days` (the ledger lines stay), the task
  artifacts older than `policy.data.retention_days` (30 days when the manifest names none) and the profile
  versions past the newest ten (`policy_overrides.retention.profile_versions`), the live ones kept. A conversation's
  clock starts when it is opened, so one that never had a turn is purged too (an older one with no
  `retention_until` and no turn is purged by its creation date).
- **Reads journal.** Each tool call of an assistant's conversation writes one line (person, need, operation,
  subject, moment; never the content). A task run's lines name the work item and the task, a delegated one the
  delegation, and a poll writes one line per poll (task and delegation; the work item too when the poll started
  exactly one run). `GET /api/v1/orgs/{org}/assistants/{slug}/reads` lists them for `assistant:audit` (owners, the
  administrator, privacy officers), filtered by `subject_type` / `subject_key` / `user_id` / `need` and the
  period (`since`, `until`), newest first, paginated by cursor. Reading the journal is itself written in the audit
  log (the filters, the subject's key as a digest, how many lines were returned). The audience of a token
  principal (machine or personal token, REST or MCP door) is judged on the user's groups at each request, exactly
  like a session's.
- **Erasure by user.** `DELETE /api/v1/me/assistants/{org}/{slug}` removes the caller's conversations and
  memories in that installation, their standing delegations (revoked first), task subscriptions, the artifacts of
  their task runs (the work item stays as the trace; the bytes of a file artifact stay in the object store, which
  has no delete) and their connected accounts at the connectors the installation binds with `user_consent`
  (revoked at the source when it offers it), and is written in the audit log. The person's journal lines stay as
  the accountability record (ADR 0133 point 6 lists no deletion of it) but lose who: their `user_id` becomes
  `erased-user:<random>`, one token per erasure, not derived from the id.
- **Erasure and export by subject.** `POST /api/v1/orgs/{org}/assistants/{slug}/erasure` with
  `{subject: {type, key}, mode: export | erase}`, for `assistant:audit`, only when `policy.data.erasure` lists
  `by_subject`. `export` returns every person's memory of the subject, the conversations where it was
  resolved and the artifacts (inline content, or the storage key of a file) of the task runs whose reads were about
  it; `erase` deletes those memories and redacts those conversations (`conversations: delete` deletes
  them), and deletes the artifacts of the task runs whose reads (the reads journal) were about the subject.
  Delegations and connected accounts belong to a person, not a subject. Both are audited, the key as a digest.

## Pack and index

```
choregos plugin validate hello/
choregos plugin pack hello/ -o hello-1.0.0.tar.gz
choregos plugin index plugins/ -o index.yaml --oci-repository ghcr.io/acme/choregos-plugins
choregos plugin index plugins/ -o index.yaml --git-url https://github.com/acme/agents \
    --git-commit <40-hex sha> --git-path plugins
```

The **content digest** (`sha256:…`) identifies a plugin. It is the sha256 of a canonical tar of its
declared files (nothing else is in the archive): entries sorted by path, regular files only, mode 0644, time 0, owner and group 0. The same
files always give the same digest, whatever their times or permissions on disk, and `pack` writes the
same bytes every time. An index (`plugin-index.schema.json`) records, for each version, its digest, its
`core` range, where to fetch it — an OCI reference, or a git repository pinned to a commit — and the
`agents` it ships (written by `choregos plugin index`): the workflow catalogue reads it to name the plugin
of an agent a workflow needs and the organisation lacks, without fetching any plugin
([ADR 0111](adr/0111-a-workflow-catalogue.md)). A
branch or a tag is not accepted in place of a commit.

## Installing into an organisation

An organisation administrator installs, upgrades and uninstalls (ADR 0074); every member lists:

```
GET    /api/v1/orgs/{org}/plugins
POST   /api/v1/orgs/{org}/plugins/install        {"source": "varga", "name": "hello", "version": "^1"}
POST   /api/v1/orgs/{org}/plugins/{name}/upgrade {"version": "1.1.0"}   # absent: the newest
DELETE /api/v1/orgs/{org}/plugins/{name}
GET    /api/v1/orgs/{org}/plugin-approvals
POST   /api/v1/orgs/{org}/plugin-approvals/{id}/approve
```

In the console: **Library → Plugins** (sources, what they offer, what is installed, the requests waiting
for a second administrator).

`source` is one the deployment declares ([Sources and trust](#sources-and-trust)), or `offline`; the
version is exact or a range, and the newest version inside it that this core admits is installed. Every
install and upgrade fetches through `fetch_plugin`, so every check of the table below applies; a refusal
is a 422 naming the check (`signature`, `digest`, `lock`…), a plugin or version the source does not
offer a 404.

**A plugin that brings a server, or a skill the gate rates high risk, waits for a second
administrator** (ADR 0050). Its install or upgrade
is checked in full, nothing is written, and the answer is `202 {"status": "pending_approval",
"approval_id", "reason"}`. Asking again answers the same request. Another administrator of the
organisation approves it with `POST …/plugin-approvals/{id}/approve` (the one who asked gets a 403); the
`reason` lists every cause: each server, and each high-risk skill with the findings that rate it so
(`script`, `executable`, `outbound_url`, `mcp_reference`, or any critical one);
the plugin is then fetched again at the recorded version and installed only if its content digest is
the one that was checked (a 409 otherwise: ask again). The install is recorded as the requester's.
`GET …/plugin-approvals` lists the requests, waiting and approved, to every member.

**Before anything is written**, each agent passes the full `AgentCreate` model, its instructions must
render and its network must be one the organisation allows; each skill passes the skill library's
validator; each eval suite is played on its agent's instructions and must pass; a procedure id the
platform or another plugin already has is a conflict; a plugin named in `requires.plugins` must be
installed. Any refusal leaves the organisation unchanged.

**What it installs:**

| Item | Where it goes | Its name in the organisation |
| :-- | :-- | :-- |
| agents | the agent registry, version 1, scope `platform`, author `plugin:<plugin>/<slug>@<catalogue version>` | its slug |
| skills | the skill library, version 1, author `plugin:<plugin>/<skill>@<plugin version>` | its name |
| templates | kept with their files | `plugin:<plugin>/<template>`, usable as a project's `template_ref` |
| evals | kept; played at every install and upgrade | `<agent>/<file>` |
| matrix cases | kept, never played at install; the live eval matrix reads them | `plugin:<plugin>/<file stem>` |
| procedures | kept; `GET /procedures` lists them for the organisation's members | their id |
| servers | run in the plugins namespace and governed as an organisation `mcp` connector ([Servers](#servers)) | the connector's name is the server's |

An agent or skill the organisation already has under that name, and that the plugin did not install,
wins: the plugin leaves it alone.

The files are kept in the database (`org_plugin_files`), keyed by the plugin's content digest and under
the organisation's row-level security, so a private plugin's text is not readable from another
organisation and an install that fails writes no file. The object store would offer neither.

**Upgrading** publishes the next version of an agent or skill only when its text changed. One the
organisation changed since (a version the plugin did not write) is **offered** the update in the answer
(`offered`) and never overwritten. Project pins do not move.

**Uninstalling** deletes what the plugin installed and nobody changed or pins. What an organisation
changed, what a project pins and a skill a kept agent still carries stay, as the organisation's own
(scope `org`). The answer lists what was removed and what was kept
(`{"name", "version", "removed": {"agents", "skills"}, "kept": {"agents", "skills"}}`), as does the
`plugin.uninstall` audit entry; a request still waiting to upgrade it is dropped. A plugin another
installed plugin requires cannot be uninstalled (409).

## Servers

A plugin brings code only as an MCP server image (ADR 0096). Installing the plugin (after the second
approval) runs each of its servers and governs it:

- **an organisation connector named after the server.** `servers/servicenow.yaml` with
  `name: servicenow` becomes the `mcp` connector `servicenow`, so an agent names it in `mcp_servers`
  (`{connector: servicenow, tools: [...]}`) and its tools are **`<server>__<operation>`**
  (`servicenow__search_records`). An organisation that already has a connector of that name gets a 409
  when it asks for the install: rename or delete it first. The connector goes with the plugin: the
  connector route refuses to delete it (409), uninstalling removes it, its operations and its secrets.
- **one operation per declared operation, at its floor.** A `read` is allowed, a `write` needs an
  approval (a governed action, ADR 0035); the administrator may change the policy, the groups and the
  price as for any connector, but an operation marked `human_approval: true` never goes below
  `approval` — the route refuses it and the broker re-applies the floor whatever a row says. A tool the
  server serves that `operations` does not list is refused: discovery does not add it, the broker does
  not offer it, a governed action does not play it. Discovery learns a declared tool's input schema; a
  later change of that schema closes it, as for any MCP server.
- **compensation and platform arguments.** `compensation: {operation, arguments}` is attached to the
  governed action of the write, its `arguments` rendered from the write's own result
  (`{sys_id: "{{ result.sys_id }}"}`), and played if a later step of the action fails.
  `platform_arguments: [action_key]` makes the platform fill an idempotency key stable for one action
  and call (`work_item_key`: the action's work item); whatever the agent wrote under those names is
  dropped.

### What the server must do

- **Refuse every call without the platform's token.** The platform generates a token per server and
  organisation, seals it in the organisation's secret store (ADR 0058), gives it to the pod as
  `CHOREGOS_SERVER_TOKEN`, and presents it as `Authorization: Bearer <token>` on every call to
  `paths.mcp`. A server that answers without it lets anything in the cluster that reaches it act with
  its credentials.
- **Answer MCP over HTTP** (`protocol: mcp-http`) at `paths.mcp`, and `200` at `paths.health`
  (readiness and liveness probes).
- **Run as a non-root user with a read-only root filesystem**; `/tmp` is a small writable `emptyDir`.
  The pod has no ServiceAccount token, drops every capability and runs under the deployment's
  RuntimeClass (gVisor when it sets one).
- **Read its configuration from the environment**: each `config_schema` property and each
  `secret_fields` entry reaches it as the variable named by the property's `x-env`, else its name in
  upper case (`base_url` → `BASE_URL`). A list of scalars is joined with commas, an object is JSON.
  `CHOREGOS_*` names are the platform's.

### Configuring a server

The connector's configuration **is** the server's: `PATCH /orgs/{org}/connectors/{server}` with
`config` (checked against `config_schema`, required fields aside) and `secret_values` for the
`secret_fields` (sealed, never shown again; `PUT …/secrets/{field}` works too). Every change is applied
to the server's Secret, and the digest of the configuration on the pod template rolls the Deployment.
Until the configuration satisfies the whole schema the Deployment has no replica and the connector
says `unconfigured`; then `deployed`. The connector routes answer the server's plugin, version, `config_schema` and
`secret_fields` under `server` (null for any other connector), so the console draws the server's own form.

### Network

The server's pods admit only the API's pods (`plugins.servers` puts the API's namespace in the policy).
Their egress is the hosts their configuration names (`egress.from_fields`, or the fixed `egress.hosts`):
each is checked against the organisation's network bounds when it is typed (a denied host is a 422), and
the server's NetworkPolicy lets through DNS and public addresses on those hosts' ports (never the
cluster's, the node's or the metadata endpoint's addresses). Kubernetes cannot name a host: with
`plugins.servers.egress: cilium` a `CiliumNetworkPolicy` narrows egress to those exact names.

### On its fake, for the eval matrix

A server may declare how the platform runs it on its fake for the live eval matrix (#1372):

```yaml
fake:
  env: {SERVICENOW_FAKE: "1"}   # what switches it to its seeded fake instance
  scenes: [default]             # the scenes that fake serves (`default` when absent)
```

Its variables are not configuration: none may be `CHOREGOS_*` or one a `config_schema` property or a
`secret_fields` entry reaches, so no administrator switches a server to its fake. Where the deployment
serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES=1`), and only there, the platform runs beside the server
its **eval instance**: the same image, the fake's variables and the platform's token, nothing of the
configuration, no egress, one replica (`<server>-fake-<org digest>`); the next reconcile deletes it once
the switch is off. A connector reaches the eval instance only when the live matrix's seeding bound it
(`fake_scene: <scene>` in its configuration), which every declaration and edit refuses (422); without the
switch the field is ignored and the connector is the server's own instance. The seeding binds only a
server's connector nobody configured, available to the selected projects and turned on for its eval
projects.

### Images

A server's image is pinned by digest (the contract), comes from a registry prefix the operator lists in
`plugins.servers.registries`, and carries a trusted signature when `images.hostedCosign` is `enforce`
(`warn` logs `plugin.server.signature_unverified`). A deployment without `plugins.servers.enabled`
refuses a plugin that brings a server. In the cluster, the Kyverno policy
`choregos-plugin-server-images` (`infra/policies/plugin-servers.yaml`) refuses a pod of the plugins
namespace whose image is not pinned or not from the allowed prefix.

## The tracker profile

A server that declares `implements: [tracker]` can be a project's tracker (plan S43 Decision 3, S43-07):
the core's `tracker/plugin` adapter maps `TrackerAdapter` onto eight tools the server serves. Their
arguments and answers are normative: `packages/contracts/schemas/plugin-tracker-profile.schema.json`
(`$defs/<tool>_input`, `$defs/<tool>_output`); the adapter checks every answer against it and refuses
one that does not match. Each answer is a JSON object (MCP `structuredContent`, or a text part holding
it); a refusal is an MCP error (`isError: true`) whose text starts with `refused:`. The first server
written against it is ServiceNow's (`choregos-agents`, `plugins/servicenow/server/TRACKER_PROFILE.md`).

| `TrackerAdapter` | Tool | Declared access |
|:--|:--|:--|
| `fetch_item(key)` | `tracker_fetch_item {key}` → the core's `WorkItemData` | `read` |
| `create_item(data)` | `tracker_create_item {title, body, labels, assignees, fields, action_key}` → `{key, created}` | `write` |
| `set_state(key, mapping)` | `tracker_set_state {key, status, label, remove_labels}` → `{key, changed, state?, ignored?}` | `write` |
| `comment(key, markdown)` | `tracker_comment {key, markdown, action_key}` → `{key, id, written}` | `write` |
| `upsert_status_comment(key, markdown, marker)` | `tracker_upsert_status {key, markdown, marker}` → `{key, written}` | `write` |
| `close_item(key)` | `tracker_close {key, close_notes, close_code}` → `{key, closed, state?}` | `write` |
| `list_candidates(project)` | `tracker_list_candidates {limit}` → `{keys, reason?}` | `read` |
| webhook | `tracker_parse_event {headers, body}` → `{verified, events: [{kind, item_key, delivery_id}]}` | `read` |

`link` and `set_fields` are not in the profile: the adapter skips them (a field write is an agent's
governed operation of the server). The loader refuses a tracker server that does not declare one of the
eight tools in `operations`, or declares a write as a `read` (that would allow it to every agent without
approval).

- **Idempotent writes.** `action_key` is derived by the platform from the call (`platform:<hash>` of
  the tool, the key and the text): a Temporal retry replays the same key and the server answers the
  first write (`created: false`, `written: false`). The same comment twice on an item is written once.
  `tracker_upsert_status` adds a note only when the latest one with that `marker` differs (journals that
  cannot be edited); Choregos's markers (`<!-- choregos:status -->`) reach the server as their name
  (`choregos:status`).
- **Platform calls, audited, not governed.** The adapter calls the server through the platform's client
  of it (the Service URL the platform wrote, the server's bearer token, no redirect), not through an
  agent's tool selection nor a governed action — as the platform's writes to Jira or GitHub. The workflow
  already decided each write (a mirrored state, a status comment, a delivered item closed after its
  review stage); an approval per write would put a person in front of every transition. Each write is
  an audit entry `tracker.plugin.<tool>` of the organisation (written in its own transaction: the write
  happened in the server's system whatever happens next). An agent calling the same tool goes through
  the broker under the declared policy, like any operation.
- **A project's tracker.** A template that says `requires.connectors.tracker: plugin:<plugin>/<server>`
  gives a project born from it the tracker connector `{type: plugin, config: {plugin, server}}`; the
  creation is a 422 while the plugin is not installed in the organisation or the server is not
  `deployed` (configure it first). `PUT /projects/{id}/connectors/tracker` with `type: plugin` checks
  the same and accepts `plugin` and `server` only: where the server answers and its token are read from
  the organisation's connector each time the adapter is built, never typed nor stored on the project.
- **Webhooks.** A procedure gives `{{plugin_webhook_url:<plugin>/<server>}}`; the system posts its
  deliveries there. `POST /webhooks/plugins/{org}/{plugin}/{server}` forwards the headers (without
  `Authorization`, `Cookie`) and the body to the server's `tracker_parse_event` over `/mcp` — the
  server's only door the platform authenticates (ADR 0096 §6), so a server's own `webhook.path` is not
  called by the core. The URL names the organisation, so a delivery reaches only that organisation's
  server (401 if the server does not recognise it, 404 if the organisation does not run that server as a
  tracker with a webhook). Each event is de-duplicated on its `delivery_id`, then its
  item is **re-read** with `tracker_fetch_item`: nothing of the delivery but the key is trusted. The
  event goes to the organisation's project that holds the item, or its only project on that tracker.
- **Polling.** `list_candidates` is the profile's `tracker_list_candidates`, read by the tracker
  reconciliation like any external tracker's, for systems that send no events.
- **Fakes.** Under `CHOREGOS_FAKES=1` a plugin tracker is built as itself and talks to
  `FakePluginTrackerServer` (`choregos_adapters.fakes.plugin_tracker`), an in-memory server of the
  profile with the same replay rules.

## Default plugins

A deployment lists plugins every organisation gets (`defaultPlugins`, the chart's
`global.plugins.defaultPlugins`, see [deployment](deployment.md#plugins)). The API installs them at
start-up in every organisation and in each organisation created later, idempotently, through the same
checks; they are listed with `default: true` and `installed_by: null`. One the gate sends to a second
administrator (a server, a high-risk skill) is not installed by the platform: it waits in
`GET …/plugin-approvals` (`default: true`, no `requested_by`) until an administrator of the organisation
approves it — unless the entry says `preapproved: true`.

- **`preapproved: true`** is the operator's approval, given in the deployment's configuration and so
  through that configuration's own review. The reconcile installs the plugin and records
  `plugin.default.preapproved` in the audit trail, with the gate's reasons and `approved_by: deployment
  configuration`. The API takes no such flag: a person's install always waits for the second
  administrator, and without the flag a default plugin waits too.
- **Offline first.** A default of a declared source is taken from the offline directory when it holds a
  version inside the requested range — no network at start-up, the content digest the source published
  — and recorded under its declared source, which serves its upgrades.

### The built-in agents

The platform's own agents left the core (ADR 0093 §6, S43-09) for the open-source repository
[`VargaFoundation/choregos-agents`](https://github.com/VargaFoundation/choregos-agents):

| Plugin | What it ships |
|---|---|
| `software-delivery` | the 19 delivery agents (triage to the production go/no-go) and the skill `madr-4` |
| `netops` | `netops`, through the organisation's pfSense, Cilium and nginx connectors |
| `authoring` | `agent-drafter` (the agent draft of the console) and `knowledge-distiller` |
| `mcp-clients` | the six MCP clients of the Integrations page (ChatGPT, Claude, Copilot, Cursor) |
| `requests` | `brief-writer` (role `brief`) and `assistant` (role `deliver`), the agents of `default-request` (ADR 0110) |

They are published, signed, to `ghcr.io/vargafoundation/choregos-agents/<plugin>:<version>` with the
index `oci://ghcr.io/vargafoundation/choregos-agents/index:latest`. The core ships their archives
offline in `plugins/offline/`, pinned by `plugins.lock` (`make plugins-lock` refreshes them, with the
network; `make plugins-check` checks them without), and the API image copies them to
`/opt/choregos/plugins`. The chart installs all five by default; the first four pre-approved (`madr-4` links to
adr.github.io and is rated high risk), `requests` needing no second administrator.

The **agent catalogue** (`/orgs/{org}/agent-catalogue`, the console's catalogue page) is a view over
plugins: the offline directory's, each replaced by the version the organisation installed. Installing an
entry from it installs that one agent (and its skills) with its plugin's provenance,
`plugin:<plugin>/<slug>@<version>`, as the plugin itself would; a template's `catalogue:<slug>` and a
workflow that names an agent resolve through the same view. Organisations that installed catalogue
agents before S43-09 had their provenance rewritten by a migration, and an `org_plugins` row recorded
for each plugin they held agents of, so upgrades keep reaching them.

The full reference of the commands is in [the CLI reference](cli.md#choregos-plugin).

## Sources and trust

A deployment declares where plugins come from in the file `CHOREGOS_PLUGINS_CONFIG` names (the chart
writes it from its `global.plugins` values, [deployment](deployment.md#plugins)):

```yaml
sources:
  - name: varga
    index: oci://ghcr.io/vargafoundation/choregos-agents/index:latest   # or https://…/index.yaml
    trust:                      # a keyless signer, regular expressions matched whole…
      identity: https://github\.com/VargaFoundation/choregos-agents/\.github/workflows/publish\.yml@refs/heads/main
      issuer: https://token\.actions\.githubusercontent\.com
      # key: /etc/choregos/plugins/varga.pub   # …or a public key cosign reads
      # tlog: false             # only for a key whose signatures never reached Rekor
    egress: [registry.acme.example]   # hosts beyond the index's own
defaultPlugins:
  - {source: varga, name: servicenow, version: "^0.1"}
  - {source: varga, name: software-delivery, version: "^1.0", preapproved: true}
offline: /opt/choregos/plugins  # or CHOREGOS_PLUGINS_OFFLINE; both, and different: the API does not start
```

Every source has a trust policy; there is no unsigned source. `GET /orgs/{org}/plugin-sources` lists
the sources, the offline directory as the source `offline`, each with what its index offers and
whether this core is inside each version's `core` range. A source whose index cannot be read is listed
with the reason.

### What a fetch checks

A fetch (`choregos_api.services.plugin_sources.fetch_plugin`, which install and upgrade call) refuses
at the first failed check, naming it:

| check | refuses |
|---|---|
| `version` / `not_found` | a plugin the index does not offer, or no version inside the requested range |
| `core` | every matching version requires another core (the index's `core`, then the manifest's) |
| `signature` | an OCI release whose manifest carries no signature by the source's identity and issuer (or key); an `oci://` index that is not signed so; a git release from an unsigned `https://` index |
| `git_ref` | a git release that is not pinned to a 40-hex commit (the index schema refuses it too) |
| `digest` | fetched files whose canonical-tar digest differs from the index's |
| `lock` | an offline plugin that differs from `plugins.lock`, or is not in it |
| `content` | a file the loader refuses, or a manifest whose name or version is not the index's |
| `fetch` | a host that did not answer, or that the egress policy refuses |

- **OCI.** A plugin is an artifact with one layer of type `application/vnd.choregos.plugin.v1.tar+gzip`
  (the `pack` output); an `oci://` index is an artifact with one layer of type
  `application/vnd.choregos.plugin-index.v1+yaml`. Cosign verifies the manifest digest, as for hosted
  images (ADR 0080 §8); the layer's bytes are checked against the manifest, then the content digest
  against the index. Publish with `oras push <ref> plugin.tar.gz:application/vnd.choregos.plugin.v1.tar+gzip`
  then `cosign sign <ref>@<digest>`.
- **Git.** A git commit carries no cosign signature, so a git release is trusted through its index: it
  is fetched only from an `oci://` index that verified. Only `github.com` and `gitlab.com` are fetched
  (the skill import's tarball fetcher).
- **Offline.** Plugins as directories or `.tar.gz` directly in the directory, trusted by their presence
  in the image. With a `plugins.lock` (`plugins: {name: {version, digest}}`, other keys ignored), each
  must be in it with that digest, when the index is read and again when the plugin is fetched.

### Egress

Sources are the operator's, not an organisation's, so their fetches are not checked against an
organisation's allowlist. They go through the guarded transport (ADR 0066) under a policy that opens the
index's host, the hosts `egress` lists, the blob storage of `ghcr.io` and Docker Hub, and GitHub or
GitLab for a git release, all still bounded by the deployment's ceiling (`CHOREGOS_EGRESS_CEILING`) and
mode. An index that names an artifact on another host reaches it only when that host is in `egress`.

### The supply-chain gate

Once fetched, `assess_plugin` applies the rest of ADR 0050's gate. Each of its skills is assessed like
an imported skill, and a plugin that brings a server or a `high` skill needs a second administrator's
approval (an install or upgrade answers 202 until it is given, see
[Installing into an organisation](#installing-into-an-organisation)). That approval is the skill's
review: once given, the plugin's skills are installed approved, a high-risk one included, with their
assessment recorded.

