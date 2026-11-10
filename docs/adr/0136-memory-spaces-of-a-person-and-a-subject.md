# 0136 — Memory spaces with user and subject scopes; a subject opens only once a resolver vouched for it

- **Status**: proposed, 2026-11-10
- **Concerns**: `memory_stores` (`scope`, new columns), `memory_facts`, a new table `memory_profiles`,
  `choregos_core.memory_scope`, `services/memory_access.py`, the memory tools of a conversation, the
  routes `/me/assistants/{org}/{slug}/memory/*`; amends [ADR 0075](0075-an-agent-reads-several-memories.md);
  keeps [ADR 0007](0007-optional-memory.md) and [ADR 0051](0051-knowledge-bases.md) §1; builds on
  [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md),
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md) and
  [ADR 0135](0135-a-tool-call-may-carry-the-person-s-identity.md)

## Context

A memory is global (the organisation's, with a reach) or a project's (named, `shared` or the agent's `own`),
ADR 0075. None belongs to a person, and none is about someone. An assistant needs both: what a person told
it once (their preferences), and what it should remember about a given colleague or a given instrument for
that person. A memory about a colleague that any conversation could open by writing a name is a way around
the sources' rights: the assistant could recall about anyone what it learnt while the user had the right to
read it.

## Decision

1. **Three new scopes for an assistant's spaces.** `installation` (one per installation, read by all its
   conversations, written by its owners only), `user` (one per person and installation), `user_subject` (one
   per person, installation and subject). `memory_stores.scope` accepts them, with `installation_id`,
   `user_id`, `subject_type`, `subject_key`, `kind` (`facts | profile`), `schema`, `writes` and
   `retention_days`. A store is created at its first write, never by hand. RLS keys `user` and
   `user_subject` stores on their owner, as a personal space.
2. **Two shapes.** `facts` are `memory_facts` rows as today, with `proposed | active | rejected` and
   validity. `profile` is one JSON document per store, conforming to the manifest's schema, versioned in
   `memory_profiles` (a row per version), shown as a form.
3. **A subject is opened by a resolver, not by a name.** A `user_subject` space opens in a conversation only
   for a subject that a need marked `subject_resolver: true` returned, in that conversation, with the
   person's identity: the broker reads `subject: {type, key}` from the structured result and records it in
   the conversation's `resolved_subjects`. Memory tools refuse any other subject (`subject_not_resolved`). A
   public subject (an index, a listed instrument) declares `resolver: none`, and any well-formed key is
   accepted. A subject handed in the opening context (ADR 0139) is a hint, never resolved.
4. **Writes follow the space's rule, applied by the broker.** `agent`: active at once; `agent_proposes`:
   `proposed`, confirmed by the person in the conversation or on the memory page; `user_only`: the agent
   cannot write; `owner_only`: only the installation's owners write (installation scope). A conversation
   never writes an installation memory; a proposal aimed at it goes to the owners, as a global memory's
   proposals do. `max_facts` and `retention_days` bound a space; the oldest unpinned facts expire first.
5. **Tools and injection.** The agent of an assistant is offered `memory_search(space, query, subject?)`,
   `memory_get_profile(space)` and `memory_propose(space, subject?, content | profile_patch, reason)`. At the
   start of a conversation the `user` profile and the most relevant facts of the `installation` and `user`
   spaces are injected, within a declared token budget, framed as data. A project policy with
   `memory.enabled: false` still cuts everything, as for runs.
6. **The person sees and owns it.** A memory page per assistant shows, corrects, pins, deletes and exports,
   with one list per subject and the pending proposals. The subject of a memory has no access to it by
   default: it is the user's memory; their right of access goes through erasure and export by subject
   (ADR 0133 point 6).

## Alternatives rejected

- **A project memory shared by everyone who talks to the assistant.** One person's remark about a colleague
  surfaces in another person's conversation.
- **Opening a subject's memory when the model names it.** The model would be the access control.
- **A knowledge base per person.** ADR 0051 keeps knowledge (written by people, for all) apart from memory
  (learnt from one person).

## Consequences

- `memory_bindings` and the namespaces of ADR 0075 do not change for runs; assistants' spaces have their own
  namespaces (`org:<key>#assistant:<installation>[#user:<id>[#subject:<type>:<key>]]`), parsed and refused by
  `split_namespace` like the other new forms.
- The core never knows what a subject's type means.
