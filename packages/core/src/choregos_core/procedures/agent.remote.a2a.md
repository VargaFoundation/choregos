---
id: agent.remote.a2a
title: Add a remote agent (A2A)
applies_to: {agent: remote.a2a}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "A2A protocol specification", url: "https://a2a-protocol.org/latest/specification/"}
values: ["oauth_callback:remote"]
egress: ["the agent card's host", "the agent endpoint's host", "the token endpoint's host"]
---

## Before you start

- A **remote agent** runs elsewhere: a workflow step calls it over the A2A protocol and takes its answer
  as the step's result. It is the reverse of the MCP door. Choregos reads its **card** and pins it.
- You need the **agent card URL** (A2A 0.3 serves `/.well-known/agent.json`; newer versions an
  `agent-card.json`), the protocol version it speaks, the **skill** to call and how it authenticates.
- An organisation administrator does the first part (the credential); whoever builds agents does the
  second.
- The agent acts with whatever identity the credential carries. Choose that account with care.

## At the vendor

1. In the vendor's administration, allow the agent to be called by other applications, if it asks for it.
2. Note the card URL. If it uses OAuth and its card advertises **dynamic client registration**, you
   register nothing. Otherwise register a client at the vendor with the redirect address below and keep
   its client id and secret.
3. Authentication is one of: OAuth 2.0 authorization code with PKCE; a bearer token; or none.

## Permissions

- The credential decides what the agent may do. Prefer a dedicated account holding only the access the
  agent needs.
- OAuth redirect (callback) address to register, when the vendor needs one: `{{oauth_callback:remote}}`.
- The refresh token is sealed in the platform's secret store and never shown again; reconnecting replaces
  it.

## In Choregos

1. Administration, **Remote agents**, **Connect**: give the card URL, then sign in **as the account the
   agent will act as** and accept. The credential shows `connected` and who it is authorised as.
2. **Agents**, **New**, **Remote agent (A2A)**: paste the card URL, **Read the card**, pick the skill and
   the credential, set the timeout and the **declared cost** (A2A reports none: it is declared, not
   measured), then publish. Publication pins the card's digest.
3. Allow the hosts: a project that uses it must allow the agent's host in `sandbox.network.allow_domains`,
   and the runner pods, the API and the orchestrator must be allowed to reach the card, endpoint and
   token hosts (see the network allowlist of your platform).

## Test it

Add a two-step workflow: the remote agent, then a human review of its answer. The answer is
`response.md`, an artifact of the step; the facts it states are recorded as declared, never measured.

## When it fails

- `remote_card_changed`: the card changed since publication. Publish a new version to accept it.
- `remote_auth_required`: the credential expired or was revoked. Reconnect it.
- `remote_input_required`: the agent asked a question. It is shown; answering it from the console is not
  supported yet.
- `remote_timeout`: the remote task is cancelled; raise the timeout or narrow the task.
- A cut at about 30 s on a synchronous call: Choregos streams, so a long task still works.
- A refusal naming a host: it is not in the stage's allowed domains, or the platform's egress allowlist
  does not open it.
