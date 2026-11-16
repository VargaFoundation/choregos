# 0139 — Talking to an assistant through the MCP door and an embedded component

> **Amended (S67-00, 2026-11-12)**: point 5 extends to the developer surfaces (the terminal, a code editor): a
> write proposed in a conversation is confirmed there too, but only under the person's own session — a console
> cookie, an embedded frame token or a developer-surface session obtained by an interactive sign-in — never under
> an API, MCP, A2A, machine or run token ([ADR 0154](0154-the-terminal-is-a-surface-of-the-platform.md) §6-7).

- **Status**: accepted, 2026-11-11 (product owner's review of the same day; proposed 2026-11-10)
- **Concerns**: the MCP door (`mcp/transport.py`, `mcp/tools.py`, `mcp/caller.py`), MCP prompts, a new path
  `/mcp/assistants/{org}:{installation}`, the console's `/embed/assistants/{org}/{installation}` page, the
  `frame-ancestors` header, inline write confirmations; amends [ADR 0030](0030-an-mcp-door-for-external-clients.md);
  keeps [ADR 0105](0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md); builds on
  [ADR 0134](0134-every-person-has-a-personal-space-conversations-are-durable.md) and
  [ADR 0135](0135-a-tool-call-may-carry-the-person-s-identity.md)

## Context

The door serves work-item tools to external MCP clients; nothing lets claude.ai talk to one of the
platform's agents, and an MCP token does not open `/responses`. An internal web application can only call
`/responses` with a console cookie from a listed origin or a `*` token, which opens the whole REST API. Both
clients must reach the same agent, at the same version, under the same governance.

## Decision

1. **One brain, several surfaces.** The console, the MCP door and the embedded component open and continue
   the same conversations (ADR 0134) through the same service; none has a route of its own beyond its
   transport.
2. **On `/mcp`**: `list_assistants`, `ask_assistant(assistant, message, conversation_id?, context?)` and
   `get_conversation(conversation_id)`, announced only for installations whose audience holds the caller.
   **On `/mcp/assistants/{org}:{installation}`**: the installation as a connector of its own, with `ask`,
   `get_conversation` and the manifest's MCP prompts (`prompts/list`, `prompts/get`).
3. **The answer** is `{conversation_id, status, text, citations[], pending_actions[{id, summary,
   confirm_url}], missing_accounts[{need, connect_url}]}`. A turn longer than a threshold (25 s by default,
   below clients' timeouts) answers `status: working`; the client calls `get_conversation` again, and the
   tool's description says so.
4. **`mcp:read` is enough to converse**: a conversation reads; its writes are proposals. The door still
   decides nothing: a write answers with a `confirm_url` to the console, where the person confirms with
   their identity. The tool names stay outside `decid|approv|reject|answer`. The bearer received is the
   subject token of `user_exchange` when the identity provider accepts it.
5. **Inline confirmation (`approval: inline`)** exists only in the console and the embedded component: the
   call becomes a card (operation, readable arguments, a diff when the source gives one) that the person
   confirms with one click, and the broker executes it with their identity. It is reserved to calls made
   with the person's identity; owners may raise an operation to `console` approval (ADR 0035) and never lower
   it below the manifest's floor.
6. **The embedded component** is the console page `/embed/assistants/{org}/{installation}?context=<base64url
   JSON>` in an iframe; `frame-ancestors` is the installation's `config.embed_origins`; sign-in is silent
   (OIDC `prompt=none` on the same provider), else a button in the frame. It posts `conversation.started`,
   `action.confirmed`, `memory.updated` and `resize` to the host. The host holds no token, no model and no
   transcript. The opening context is validated against the manifest's `context_schema` and given to the
   agent as data.
7. **Choregos exposes its assistants, not other servers' tools** (ADR 0105 stands): the door never relays a
   source's tools to an external client.

## Alternatives rejected

- **A skill in the client with a connector per source.** No budget, no audit, no versions, no evals in
  Choregos.
- **A `*` token for the host application.** A key to the whole API in a web page's backend.
- **Deciding a write through MCP with a confirmation argument.** Refused by ADR 0030 point 5.

## Consequences

- Two models in cascade (the client's, then the assistant's) cost more and answer more slowly than a skill;
  it is the price of governance and is measured. MCP prompts reduce the round trips.
- A2A for assistants is left for later: no caller needs it yet.
