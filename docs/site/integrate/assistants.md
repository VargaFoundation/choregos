---
title: Use assistants from claude.ai, MCP clients and your own application
description: Talk to an assistant from an MCP client such as claude.ai, and embed it in an internal web application.
sidebar_position: 5
---

# Use assistants from claude.ai, MCP clients and your own application

An [assistant](../use/assistants.md) your organisation installed for you is not limited to the console.
Two doors open the same conversations, in your personal space: the **MCP door**, for claude.ai, Claude
Code and any other MCP client, and an **embedded component**, for a panel inside an internal web
application. The assistant, its sources and its budget stay on the Choregos side. At `acme`, the
billing assistant is reachable from claude.ai and from the finance team's intranet.

**What you will be able to do:** list and ask assistants from an MCP client, follow an answer that takes
time, send a person to confirm a write, and frame an assistant in your application.

**Before you start:**

- an account in the audience of at least one `ready` assistant;
- for an MCP client: the connection of [Connect your AI assistant (MCP)](mcp.md). A token with `mcp:read`
  is enough to converse: a conversation reads, and its writes are proposals;
- for the embed: an owner of the installation to list your application's origin.

## From an MCP client

### The tools

On `https://choregos.example.com/mcp`, three tools are announced only to people who are in the audience
of at least one ready assistant:

| Tool | What it does |
| --- | --- |
| `list_assistants` | the assistants you may talk to, as `org:slug`, with what each one does |
| `ask_assistant` | sends a message to one assistant (`assistant` as `org:slug`, `message`, and optionally `conversation_id` to continue, and a `context` object) |
| `get_conversation` | reads a conversation by its `conversation_id` |

Each installation is also its own connector, with its own address:

```text
https://choregos.example.com/mcp/assistants/acme:billing
```

There the tools are `ask` and `get_conversation`, and the assistant's own MCP prompts (a ready-made
question with arguments) are served as MCP prompts. Outside the audience, that address answers `404`.
Choregos exposes its assistants only, never the tools of other servers.

A conversation opened here is one of your conversations in the console: you can continue it there.

### Read an answer

An answer carries `conversation_id`, `status`, `text`, `citations`, `pending_actions` and
`missing_accounts`.

- A turn that takes longer than 25 seconds answers `status: working` and goes on in the background.
  Call `get_conversation` with the `conversation_id` until the status is `completed` (or `failed`).
- `citations` are the documents of the assistant's knowledge it used, with their title and version.
- `pending_actions` are the writes it proposes and you have not decided. Each has a `confirm_url`.
- `missing_accounts` are the accounts it needed and you have not connected, each with a `connect_url`.

### Confirm a write

The door never decides. When the assistant proposes a write, open its `confirm_url`: it leads to the
console, where you sign in and press **Confirm** or **Refuse**. The write is made with your identity.

### Connect it from claude.ai

claude.ai calls from Anthropic's network and signs in with OAuth, so the conditions of
[claude.ai](mcp.md#claudeai) apply: a public HTTPS address and OAuth on the door, set by your operator.
Then, in claude.ai under *Settings, Connectors, Add custom connector*, enter the assistant's address
above and your administrator's client ID, and sign in with your usual identity provider.

Expect two models in cascade, the client's and the assistant's: more cost and latency than a skill kept
in the client, in exchange for budget, audit, versions and evals. The assistant's MCP prompts save round
trips.

**What you should see:** in claude.ai, the assistant answers with its citations; a write comes back as a
link, never as an action done.

## Embed an assistant in your application

A host page shows the assistant in an iframe. The host holds no token, no model and no transcript: the
person signs in to Choregos, and the conversation is theirs.

1. Ask an owner of the installation to list your application's origin in the installation's
   `config.embed_origins`: origins only (`https://intranet.acme.example`), no path, no `*`, and `http`
   only for `localhost`.
2. Put the frame in your page:

```text
<iframe title="Billing assistant" width="480" height="520"
  src="https://choregos.example.com/embed/assistants/acme/billing?context=BASE64URL_JSON"></iframe>
```

3. Optionally pass a `context`: a JSON object, base64url-encoded, that tells the assistant what the
   screen shows (for example the invoice open on it). Choregos checks it against the assistant's
   `surfaces.embed.context_schema` and gives it to the agent as data, never as an instruction. An
   assistant that declares no schema refuses a context.
4. Listen to the frame's events (below).

Only the origins you listed may frame the page: the console answers with a `frame-ancestors` rule built
from them, and every other console page refuses framing.

### Sign-in

If the frame has no session, it asks the identity provider once, silently. If the provider needs the
person, the frame shows a **Sign in** button that opens the provider in a popup. The popup hands the
frame a **frame token** that lasts 15 minutes, is bound to the person, their browser session and this
installation, and opens only what the component calls. It stays in the frame and never reaches your page.
So the host may be on another site than the console: no third-party cookie is needed. When the token
expires, the button shows again.

### Events

The frame sends messages to your page with `postMessage`, only to the origin that framed it. Check
`event.origin` (the console's) and `event.data.source === "choregos.assistant"`. Each message is
`{ source, type, org, installation, ...payload }`:

| `type` | When | Payload |
| --- | --- | --- |
| `conversation.started` | the conversation is open, new or resumed | `conversation_id`, `resumed` |
| `action.confirmed` | the person confirmed a write | `conversation_id`, `action_id`, `operation`, `status` |
| `memory.updated` | a turn wrote or proposed in a memory space; add `?memory=space1,space2` to the frame to narrow it | `conversation_id`, `spaces` |
| `resize` | the content's height changed: size your iframe to it | `height`, in pixels |

The frame also shows the writes the assistant proposes, with what they would change, and **Confirm** and
**Refuse** buttons.

**What you should see:** the assistant in your page, a `conversation.started` event in your listener, and
the same conversation in the console under **Assistants**, **My conversations**.

## What can go wrong

| Symptom | Likely cause |
| --- | --- |
| the three tools are not announced | you are in no audience, or no assistant is `ready` |
| `404` on `/mcp/assistants/…` | you are outside that assistant's audience, or the installation does not exist |
| the frame is blank, the console reports a framing refusal | your origin is not in `config.embed_origins` |
| the frame says the context is refused (`422`) | it does not match `context_schema`, or the assistant declares none |
| the **Sign in** button shows again | the frame token expired, or your session ended |
| `status: working` never ends | call `get_conversation` again; a `failed` status says the turn did not complete |

## Go further

- [Connect your AI assistant (MCP)](mcp.md) for the door, tokens and limits.
- [Ship an assistant in a plugin](../build/assistants.md) to declare its prompts and context schema.
- The full contract: [integrations reference](../../integrations.md).
