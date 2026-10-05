# 0029 — Choregos is a general-purpose platform for governed agents

- **Status**: accepted, 2026-10-05 — supersedes [ADR 0028](0028-l-execution-gouvernee-du-changement.md)
- **Concerns**: what Choregos is for, the project model, agents and skills, MCP, connectors, the
  console, and what stays out of scope

## Context

On 2026-10-05 the product owner reviewed the console of the development tenant and listed five
gaps:

1. A project holds **one** workflow. The landing page still says "each project ties a repository
   to a workflow".
2. The administration page shows six cards and none of the enterprise edition's ten features.
3. Workflows are hard to read and only editable as YAML; **agents** and the **MCP servers** they
   may use are nowhere to be seen.
4. Project settings are software-delivery slots: tracker, scm, ci, cd.
5. There is no way to connect Claude, or any other LLM client, to the platform.

He then challenged the model with a human-resources project holding two workflows, onboarding and
offboarding: ship a laptop, create a Microsoft Entra account, add it to groups, configure an NFC
badge, and exchange with an external agent through MCP tools and skills.

The code answers part of it. The engine has been generic since ADR 0012: a state machine, human
actors with an SLA and an escalation, gates that refuse, budgets, and, in the ontology trial,
governed proposals with a re-authenticated decision. The product model is not generic:

- a project runs the highest active workflow version, whatever its name, and a work item never
  records which workflow it was born in;
- connector kinds are eight fixed software slots, configured as raw JSON;
- an agent is a line of workflow YAML; its instructions belong to the deployment; "skill" does not
  exist; a run reaches exactly two MCP servers;
- the `system` actor opens and merges pull requests from state-name prefixes (`pr_`, `merged`);
- a governed write to a third-party system runs inside an HTTP request, with no retry and no
  durable record of which effect already happened.

ADR 0028 had answered the scope question the other way: govern the execution of **versioned
change**, and give up an agent registry, a gateway and cross-vendor agent discovery. The analysis of
2026-10-05 recommended a middle ground — governed change of any system of record, code, accounts,
devices and access alike. The product owner chose to widen the scope further.

## Decision

1. **Choregos is a general-purpose platform for governed agents**: any business process, carried
   by agents and people, inside the customer's perimeter. Software delivery becomes one template
   among others — IT operations, joiners and leavers, a data change.
2. **In scope**, each with its own record:
   - several workflows per project, each work item pinned to the workflow version it was born in;
   - a door for external MCP clients — Claude Code first, with a scoped token; OAuth next;
   - an **agent registry**: internal agents (run by Choregos) and external agents (clients that
     connect), versioned, owned, budgeted and revocable — and a **skills library** attached to
     them;
   - **connectors by capability**, with a policy per operation; an MCP server is a connector;
   - **governed actions in the core**, executed durably by Temporal;
   - administration sections declared by manifest, so that the enterprise edition shows its
     features without the core knowing them.
3. **Governance stays the core, and the reason to choose Choregos.** Every write to a third-party
   system is an action. An approval needs a fresh authentication and a person other than the
   proposer. Evidence is measured by the platform, never taken from the agent's account. Every run
   and every tool call has a budget and a line in the cost ledger. Everything runs inside the
   customer's perimeter, whatever the agent.
4. **Still plugged into, not built**: the identity provider, the sandbox runtime (ADR 0016),
   observability (ADR 0018). There is **no general-purpose MCP gateway**: the platform brokers tools
   for the runs it governs and for the clients it authenticates, nothing else.
5. **Invariants**:
   - no secret enters an agent's pod;
   - discovering a tool never opens it;
   - an agent acting on behalf of a person holds the **intersection** of both sets of rights;
   - no decision is ever taken through MCP: a decision needs a re-authenticated session.
6. **Editions** (ADR 0024): the registry, the skills, the connectors and the MCP door are in the
   community core. The enterprise edition keeps multi-organisation, identity sync (SAML, SCIM),
   session revocation, and later the signed attestation and chargeback.

## Consequences

- Six backlog streams, S15 to S20, one per delivery batch: the MCP door, several workflows, the
  administration and the enterprise edition, agents and skills, connectors and MCP servers,
  governed actions and the HR scenario. Each story is one pull request with a test that fails
  without it.
- `docs/positioning.md`, the `README` and the agents' instructions are rewritten; the
  specification (Peredur) follows.
- The HR scenario — joiners and leavers — becomes the acceptance test of the generic model. It is
  played end to end on scriptable fakes (ADR 0005) before any real system.
- The S14 stream of ADR 0028 stays valid: data work still enters through the catalogue and the
  gates.
- The market we enter is crowded: Microsoft Copilot Studio, Google Agentspace, Dataiku's agent
  management, the agent builders of every large platform. Our claim narrows to what they do not
  ship together: open source, self-hosted inside the customer's perimeter, any agent, and
  governance by mechanisms rather than by prompts.

## What would change our mind

- **The model does not hold on real systems.** If the HR scenario cannot run against real
  identity, device and badge systems with the same code by milestone J4 (2027-02-26), the generic
  model is a demo, and we narrow back to governed change.
- **The registry belongs to the identity provider.** If customers make Entra Agent ID or Okta the
  source of truth for agents, our registry becomes a mirror of theirs, synchronised, not a
  competitor.
- **Someone ships it first.** If a vendor ships governed agents inside the customer's perimeter,
  with measured evidence and re-authenticated approvals, our edge narrows to the open core and to
  multi-tenant delivery for integrators.

## Alternatives discarded

- **Keep ADR 0028's scope** — governed execution of versioned change. It left HR, IT and
  back-office processes outside, which is where the product owner wants to go.
- **Governed change of any system of record** — the recommendation of the 2026-10-05 analysis. It
  covers the HR case as changes to accounts, devices and access, but keeps agents and MCP in the
  background; declined for that reason.
- **Building an identity provider or a general MCP gateway** — still discarded, for the reasons
  ADR 0028 gave: that fight is about capital, and it is already absorbed by identity, network and
  data vendors.
