# 0126 — An agent may write for people in another language

- **Status**: proposed, 2026-11-09 — amends [ADR 0039](0039-everything-a-user-reads-is-in-english.md)
  §1 for an agent's output only
- **Concerns**: `AgentSpec.language` (`choregos_api.schemas.agents`, `openapi.yaml`),
  `choregos_playbooks.frame` and `invariants_in`, `choregos_orchestrator.activities.stage`
  (`_render_playbook_text`), the console's agent builder, definition and version diff

## Context

ADR 0039 §1 made English everything a person reads, and the frame a registry agent's instructions
run under (ADR 0033) says it to the agent: "Write in English everything a person will read — the
summary, the documents you produce, your questions, your findings — even when the work item, its
fields or its names are in another language." The line came from a real failure: a ticket whose
fields held a person's French name made an otherwise all-English agent write its plan in French
(`packages/playbooks/tests/test_playbooks_in_english.py`).

That is right for what the platform ships. It is wrong for an organisation whose people work in
another language: the agents its own authors write produce documents, answers and questions for
those people, and the frame forbids them to write in the language they read. ADR 0039 left
internationalisation for "the day a customer asks for it"; this record does not translate the
platform, it lets an agent's author choose the language of that agent's output.

## Decision

1. **A version says the language of its output**: `AgentSpec.language`, a language tag — a primary
   subtag of two or three lowercase letters, optionally a region of two capitals (`fr`, `de`,
   `pt-BR`); anything else is a `422`. Absent means English, and is left out of the stored spec, so
   no published version's digest moves.
2. **The frame asks for it, and only the language line changes.** `frame(instructions, language)`
   replaces the English line of the invariants with the same sentence in the chosen language, named
   in English (`French`, `Brazilian Portuguese`; a tag the platform has no name for is "the language
   tagged `xx`"), and adds that what a machine reads stays as specified: the keys, the identifiers,
   the enum values of the result contract (`status`, `type`, `severity`) and the shape of
   `.choregos/result.json`. Absent or `en`, the frame is today's byte for byte. The frame itself
   stays in English: it is platform text.
3. **Every prompt of a registry agent carries it**: its framed instructions, its role's playbook
   when it has no instructions of its own, a trial of a draft version, and every runtime that runs
   a step from that prompt (`acp`, `code`, `prompt`, `hosted`). A remote (`a2a`) agent receives the
   framed instructions as its message, so the field is accepted there too: the language reaches it,
   like the rest of the frame, as a request it may honour.
4. **Per agent, not per organisation.** The language belongs to what an agent IS, beside its
   instructions: an organisation's code agent keeps writing pull requests and commit messages in
   English for the repository's reviewers while its assistant writes for people in their language.
   A setting per organisation or project would force one answer on both.
5. **What the platform ships stays English** (ADR 0039 §1 stands for it): the console, the API's
   and the validator's messages, the shipped templates, agents and skills, the frame's own words,
   and a built-in role run without a registry agent. Identifiers and machine fields stay English
   (ADR 0039 §2, ADR 0046).

## Consequences

- An author who sets `language` publishes a new version, under the same gates as any other change;
  the console's version diff shows it with the instructions.
- The guard that refuses French in the frame (`test_playbooks_in_english.py`) also reads the frame
  asking for French: it names the language in English and stays English.
- What a person reads of a run that the platform writes — status comments, the console's labels —
  stays English; only what the agent writes follows its language.
- An interactive session (chat or voice) carries the agent's instructions without the frame, as
  before, so `language` does not reach it: neither English nor another language is asked for there.
  An author who wants one says it in the instructions.

## Alternatives discarded

- **`auto`, the language of the work item**: unreliable. Detection on a short ticket guesses, and
  the failure that put the English line in the frame is exactly this one: a French name in a field
  turned an English agent's plan French. A language the author states is a decision; one the model
  infers from the input is an accident. It can come back with a measured detector.
- **A setting per organisation or project**: one answer for every agent of it, while a code agent
  and an assistant of the same organisation need different ones (decision 4).
- **Translating the frame**: the frame is the platform's contract with the agent, read by the agent
  and by no person; a translated frame would be one more contract per language to keep in step, for
  no reader's benefit.
