# 0133 — A plugin ships an assistant; an organisation installs it and publishes it to an audience

- **Status**: proposed, 2026-11-10
- **Concerns**: `packages/contracts/schemas/plugin.schema.json` (`contents.assistants`), a new
  `assistant.schema.json`, `choregos_core.plugins` (`load_directory`, `_declared_refs`), the plugin
  installer (`services/plugin_installer.py`), new tables `assistant_installations` and
  `assistant_tool_bindings`, the permission matrix (`rbac.py`), the routes `/orgs/{org}/assistants/*` and
  `/me/assistants`; amends [ADR 0093](0093-an-agent-ships-as-a-plugin.md) (a new kind of plugin content);
  builds on [ADR 0033](0033-agent-registry-and-skill-library.md), [ADR 0061](0061-agent-ownership-scopes-and-forks.md),
  [ADR 0071](0071-an-agent-version-reaches-production-through-channels.md),
  [ADR 0080](0080-hosted-prompt-and-voice-agents.md), [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md)
  and [ADR 0107](0107-an-agent-acts-under-an-identity.md); plan `docs/plan/PLAN-2026-11-10-assistants.md`
  (stream S63)

## Context

The product owner asked on 2026-11-10 for a conversational assistant that prepares a meeting with a
colleague, reads several internal sources with the user's own identity, remembers what the user asked it
to remember about each colleague, and is reached from claude.ai and from an internal web application. The
same request, written as a design test, describes a personal investment assistant. Neither fits what the
platform has:

- **An agent is a workflow's actor.** A prompt agent can be called directly (`POST
  /orgs/{org}/agents/{slug}/responses`, ADR 0080 §10), but only by someone holding `item:control` in a
  project, inside that project, for one hour, with the read tools of its grant, no knowledge and no memory.
- **Who may talk to it is a project membership.** Making every manager a `developer` of a project to let
  them ask a question gives them that project's work items and decisions too.
- **What it needs is implicit.** An agent's version names MCP connectors by the organisation's connector
  slugs. Nothing says, before a conversation, that a required source is missing, under which identity a
  source must be read, or which memory the agent keeps.

## Decision

1. **An assistant is a plugin content, not a second agent registry.** `contents.assistants` lists files of
   `kind: Assistant` (`apiVersion: choregos/v1`), validated by `assistant.schema.json`. An assistant names an
   agent of the same plugin (`agent:`), whose runtime is `prompt` or `hosted`; the agent keeps its versions,
   channels and evals. The assistant adds what an agent does not have: a configuration schema, an audience,
   the tools it requires and the identity each is called under (ADR 0135), its memory spaces (ADR 0136), the
   knowledge it offers (ADR 0138), its background tasks (ADR 0137), its surfaces (ADR 0139) and a policy
   (budgets, conversation retention, data classification and erasure).
2. **A tool is a need, bound at install.** `tools: [{need, operations, access, identity, required,
   approval?, subject_resolver?, satisfied_by?}]`. The agent sees `<need>__<operation>`, never a
   connector's slug: changing the provider of a need changes neither the instructions nor the evals.
   The binding goes through the broker as today (operation policy, schema, injection guard, ledger line).
3. **Checked at validation, then at install.** `choregos plugin validate` refuses, with the JSON path of
   the field: an agent that is not in the plugin or whose runtime is not `prompt` or `hosted`; a `{{
   config.x }}` of the instructions missing from `config_schema`; a tool the agent calls that no need
   declares; a write need with `approval: none`; an `inline` approval under the `service` identity; a
   `user_subject` memory without a subject or without a resolver (unless the subject declares
   `resolver: none`); personal data without a conversation retention and an erasure mode; a delegated task
   that calls a write need; a name that is not English `snake_case`. At install, each required need is
   bound to a connector that serves its operations and supports its identity mode; otherwise the
   installation is `incomplete`, with one readiness item per gap, and no conversation opens on it.
4. **An installation is an organisation object.** `assistant_installations` (org, slug, plugin and version,
   assistant name, `config`, `owner_groups`, `audience_groups`, the agent channel it follows, `status`
   `incomplete | ready | suspended`, policy overrides) and `assistant_tool_bindings` (need, connector,
   operations, identity, approval, enabled). Both are under the organisation's forced RLS. Uninstalling the
   plugin suspends its installations; it does not delete what belongs to users (ADR 0134).
5. **Three permissions, granted by IdP groups, not by project roles.** `assistant:manage` for the owners'
   groups (configure, bind, edit the knowledge, choose the channel, set the audience and budgets),
   `assistant:use` for the audience's groups (converse, subscribe to tasks, manage one's own memory and
   accounts), `assistant:audit` for the owners and an organisation role `privacy_officer` (the reads
   journal, erasure and export by subject). Installing, uninstalling and changing the owners stay the
   organisation administrator's, through the plugin install of ADR 0093. The audience is re-read from the
   token's groups **at every turn**: leaving a group cuts the next turn, suspends the person's delegations
   and deletes nothing.
6. **Personal data is governed by code, not by a runbook.** An assistant that declares
   `audience.personal_data: true` must declare a conversation retention and an erasure mode. A daily
   purge deletes expired conversations with their events, task artifacts and expired memory facts. Every
   tool call of an assistant writes a line of `assistant_reads` (who, which installation, need and
   operation, the resolved subject, the delegation if any, when; never the content read). Erasure
   `by_user` removes what belongs to a person in an installation (conversations, memories, connected
   accounts, delegations, artifacts); erasure and export `by_subject` (by `assistant:audit`, journaled)
   remove a subject's memories across every user and redact or delete the conversations it was resolved
   in. The platform does not decide whether a use is high-risk under the AI Act and replaces neither a
   data protection impact assessment nor a works council's consultation: it gives them evidence (journal,
   retention, versions, evals).
7. **No business vocabulary in the core.** The core knows needs, operations, subjects as `{type, key}` and
   memory spaces; it never knows what a relationship, a coach or an instrument is. A fixture plugin that is
   not the first use (`investment-assistant`, on fakes) installs and converses with no code of its own in
   the core, and CI plays it (S63-19).

## Departures from the specification of 2026-11-10

- **Numbers.** The specification proposed ADRs 0126-0133; 0126-0132 were taken on `main` or by open pull
  requests on that day, so the eight records are 0133-0140, in the same order.
- **Task workflows and knowledge seeds are files the assistant names**, not new `contents` keys: the
  plugin schema has no `workflows` key, and a template already ships knowledge bases (ADR 0117). The
  loader adds every path an assistant names (`agent` aside) to the plugin's declared files, so they are
  packed, digested and checked like a template's files.

## Alternatives rejected

- **A shared project per assistant.** Every user becomes a `developer` of it; memory and budget mix; a
  person's tokens have no place.
- **A project per user, created explicitly.** N projects to configure and upgrade, noise in every project
  list, and still no place for a person's tokens and memory. ADR 0134 keeps the project machinery but makes
  the personal space implicit.
- **An assistant as a new kind of agent.** Two registries with versions, channels and evals to keep in step.

## Consequences

- A contract change (S63-01): `assistant.schema.json`, `contents.assistants`, `make contracts`.
- The install path grows a readiness check that the console and `GET
  /orgs/{org}/assistants/{slug}/readiness` show, with who may fix each item.
- What a source lets a person read is still decided by the source (ADR 0135): the installation decides who
  may talk to the assistant, never what it may read about someone.
