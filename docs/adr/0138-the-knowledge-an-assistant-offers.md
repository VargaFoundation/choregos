# 0138 — Knowledge an assistant offers: a seed, the installation's copy, proposed upstream updates

- **Status**: accepted, 2026-11-11 (product owner's review of the same day; proposed 2026-11-10)
- **Concerns**: `knowledge_bases`, `knowledge_documents` (an `origin`), the plugin installer and upgrade,
  the hybrid index (`knowledge/index.py` `search`), the knowledge tools of a conversation, the routes
  `/orgs/{org}/assistants/{slug}/knowledge/updates`; amends [ADR 0051](0051-knowledge-bases.md); builds on
  [ADR 0081](0081-knowledge-base-rights.md), [ADR 0117](0117-a-template-ships-the-knowledge-bases-its-agents-read.md)
  and [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)

## Context

An assistant's value is in its method: written by the people of the trade, adapted by each organisation that
installs it, improved by its author over versions. A template already ships knowledge bases (ADR 0117), but
an installed base is then the organisation's alone: the plugin's next version cannot tell what the
organisation changed. And a conversation has no knowledge at all: only a run's stage pins bases
(`serving.pin`) and offers `knowledge_search` through the sidecar.

## Decision

1. **A seed is a `KnowledgeBase` directory of the plugin** (`knowledge.yaml` and a markdown `corpus/`, the
   format templates use), named by the assistant's `knowledge: [{name, seed, serving_mode, upstream}]`.
2. **The installation makes a copy.** An organisation base `<installation>-<name>`, owned by the owners'
   groups (ADR 0081, role `owner`), scoped to the installation, each document marked `origin:
   seed@<plugin version>`. A document edited, or added, by the owners becomes `origin: local`. Sources
   (SharePoint, Confluence, git, web) may be added with today's screens.
3. **Upstream updates are a three-way comparison per document** (previous seed, new seed, local copy). A
   document unchanged locally follows `upstream`: `follow` updates it, `propose_updates` makes a proposal,
   `frozen` does nothing. A document changed on both sides is always a proposal, with its diff, that the
   owners accept, merge or refuse.
4. **A conversation reads the installation's bases, and only those.** The agent is offered
   `knowledge_search(base, query, top_k)` and `knowledge_get(base, document_id)` on the existing hybrid
   index; every passage carries its document and version, and the agent must cite what it uses. The bases
   are served to the installation's conversations whatever the person's groups: giving the person the
   assistant is the control. A base outside the installation is never offered in it, even to a person who
   may read it elsewhere. Tasks keep today's `files` or `index` serving.
5. **Knowledge is not memory.** What holds for everyone and is written by people is knowledge; what the
   assistant learns from one person is memory (ADR 0136).

## Alternatives rejected

- **Reading the plugin's seed directly.** The organisation could not adapt the method, and an upgrade
  would silently change it.
- **Overwriting on upgrade.** It destroys the owners' work.
- **Serving every base the person can read.** The assistant's behaviour would depend on who asks, and its
  evals would test nothing stable.

## Consequences

- `knowledge_documents` gains an origin and the seed digest it came from.
- Upgrading a plugin may leave proposals the owners have to decide; the installation stays usable meanwhile.
