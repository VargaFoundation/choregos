# 0159 — An assistant may read the organisation's knowledge bases by reference

- **Status**: accepted, 2026-11-12 (the product owner's amendment of the same day)
- **Concerns**: `assistant.schema.json` (`knowledge[].bind`), a new table `assistant_knowledge_bindings`, the
  installation's routes (`POST /orgs/{org}/assistants`, `PUT /orgs/{org}/assistants/{slug}/knowledge-bindings/{name}`),
  its readiness (`services/assistants.py`), the knowledge of a conversation (`services/conversation_knowledge.py`
  on `knowledge/index.py` `search`), the need-change path of a plugin upgrade (S63-22); amends
  [ADR 0138](0138-the-knowledge-an-assistant-offers.md) (points 1 and 4); builds on
  [ADR 0051](0051-knowledge-bases.md), [ADR 0081](0081-knowledge-base-rights.md),
  [ADR 0097](0097-a-run-reads-a-restricted-base-on-behalf-of-its-initiator.md) and
  [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)

## Context

An assistant gets knowledge only from a seed its plugin ships, which the installation copies (ADR 0138). An
organisation that already keeps knowledge bases (ADR 0051), fed by their sources, with owners and readers
(ADR 0081), has to copy them to give them to an assistant: a duplicate nobody syncs, whose rights are the
installation owners' instead of the base's. The plugin's author cannot ship that knowledge: it is the
organisation's, and only the organisation knows which of its bases answers the need. A tool need has the
same shape and is already solved: the manifest declares it, the installation binds it to a connector
(ADR 0133 point 2).

## Decision

1. **A knowledge item is a seed or a binding need.** `knowledge: [{name, seed, serving_mode, upstream}]` stays;
   an item may instead declare `{name, bind: {description, required}}`: what the organisation must point the
   assistant at, in the author's words, and whether the assistant is usable without it (`required`, default
   true, as for a tool need). Exactly one of `seed` and `bind`; `serving_mode` and `upstream` are a seed's
   only (a bound base keeps its own serving mode and sources). Names stay unique across the list.
2. **Bound by reference, never copied.** At install (`knowledge_bindings: {name: [base, ...]}` in the body, the
   organisation administrator's gesture) and later by the installation's managers (`assistant:manage`, `PUT
   .../knowledge-bindings/{name}` with the whole list, idempotent, at most 20 bases), a need is bound to one or
   more existing bases of the organisation. A row of `assistant_knowledge_bindings` names the installation, the
   need and the base; nothing is copied and nothing of the base changes: its owners, grants, reach, sources and
   serving mode stay its own. The assistant reads it; it never writes it (a base is read-only for agents, ADR
   0051 point 1).
3. **The binder reads what they bind.** Adding a base to a need requires that the caller may read it now
   (`knowledge.access.decide(..., read)`, ADR 0081) and that it is `active`. A base the caller may not read and
   a base that does not exist get the same 422, naming the slug they sent: binding is not a way to learn that a
   base exists. Keeping a base another manager bound, or removing one, needs no right on it. The managers of
   the installation see the slugs of every bound base (they must be able to remove one). Audited as
   `assistant.knowledge.bind`, with the bases added and removed.
4. **The person reads it at every turn, or the turn drops it.** A bound base is offered to a conversation's
   turn only when the person of the conversation may read it at that moment, by the one function of ADR 0081
   (`visible_to`, with the base's grants), the person's roles and groups rebuilt from the database as ADR 0097
   does for a run's initiator. A base the person may not read is dropped from that turn: not named in the
   tools' descriptions, not searchable, not readable by id; nothing of it reaches the answer, the citations or
   the MCP door. The binding stays, and the next turn checks again (a grant given, a group joined, a base
   restricted: the next turn follows). A seeded copy keeps ADR 0138 point 4: it is the installation's own
   base, served to the whole audience.

   **Why per turn, and not "the audience must be inside the readers" at bind time.** The audience is a list of
   IdP groups; who reads a base is its reach, its `restricted` flag, its grants (to persons, groups and
   projects) and the organisation's roles. The two cannot be compared as sets when the binding is made, and
   both change after it: a check at bind time would be wrong the next day and would still need the per-turn
   check. Serving a bound base to the whole audience instead would make an installation a way around ADR
   0081: an owner could hand a restricted base to people its owners never let read it.
5. **Readiness.** A `required` binding need with no enabled binding to an `active` base is a readiness item
   `knowledge_unbound` that the owners fix, and the installation is `incomplete` until it is bound, as for a
   required tool need. An optional need left unbound is not a gap. A deleted base leaves its bindings with it;
   an archived one is not served and does not count as bound. The readiness does not, and cannot, say whether
   each person of the audience reads the bound bases (point 4).
6. **One search over both.** The conversation's tools (`knowledge_search`, `knowledge_list`, `knowledge_get`,
   S63-05) search the installation's seeded copies and its bound bases alike, through the same hybrid index;
   every passage, every document read and every citation names its base by its slug.
7. **Upgrades follow the need-change path of S63-22.** A binding need the new manifest drops (or turns into a
   seed) has its bindings disabled and kept, and is no longer served; one it adds is unbound, a readiness item
   when required; a `required` flag that moved is reported. A rename is a drop and an add. The owners are told
   in the same notice as the tool needs. A need that comes back finds its bindings disabled: binding it again
   enables them.

## Alternatives rejected

- **Copying the organisation's base into the installation** (what a seed does). The duplicate drifts from its
  sources, and its readers become the installation's audience instead of the base's.
- **A new reach `installation` on the base** (ADR 0051 point 2). A base has one scope; giving it to an
  assistant would take it away from the runs that read it.
- **Requiring the audience to be inside the readers when binding.** Not computable as sets, and wrong as soon
  as either side changes (point 4).
- **Serving a bound base to the whole audience** (ADR 0138 point 4 extended). An owner could expose a restricted
  base to people who may not read it.

## Consequences

- Two persons of the same audience may get different answers from one assistant when a bound base is read by
  one and not the other. That is the price of keeping the base's rights; owners bind the bases their audience
  reads, and conversation suites (ADR 0140) play on the installation's bases as their evaluator may read them.
- A table `assistant_knowledge_bindings` under the organisation's forced row-level security; a contract change
  (the manifest's `bind`, the install body, the binding route, the readiness kind, the installation's
  `knowledge_bindings` and the plugin assistant's `knowledge_needs`).
- ADR 0114 does not move: a conversation's answer is read by its one person, who reads the base at that turn. A
  proposal to an installation memory goes to its owners, as every such proposal does (ADR 0136 point 3).
