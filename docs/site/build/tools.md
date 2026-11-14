---
title: Give agents tools and network access
description: Add an MCP server, open its tools one by one, grant them to an agent, and decide which hosts a run may reach.
sidebar_position: 4
---

# Give agents tools and network access

Your triage agent would answer better if it could search the team's design documents, and your
implementer needs the package registry. An agent reaches nothing by default: every tool and every
host is opened on purpose, at the narrowest level that works, and every call is journaled.

**What you will be able to do:** add an MCP server to your organisation, open its tools one by one,
grant them to an agent, require a person's approval for writes, and allow the hosts a run may reach.

**Before you start:**

- an organisation administrator, to add servers and open tools and hosts;
- an [agent](agents.md) to grant the tools to.

## How a tool call is governed

Three levels each say yes before a call happens, and each one can only narrow the one above:

1. **The organisation** adds the server and sets each tool's policy: `allowed` (agents run it without
   asking anyone), `approval` (agents run it once a person approves), `forbidden` (nobody runs it).
2. **The project** sees the organisation's tools and may only tighten them: an `approval` cannot become
   `allowed` in a project.
3. **The agent's version** names the tools it takes, by name or pattern.

The run calls the tool through the platform, never directly: the arguments are checked against the
tool's schema, the call counts against the project's `tool_calls_per_run`, the server is called with
the connector's own key (the run never holds it), the cost is recorded, and the answer passes the
prompt-injection guard before the agent reads it.

## 1. Add an MCP server

**From the catalogue:** sidebar **MCP servers** lists the servers the platform knows; **Install**
asks for the credential and whether the server is available to every project or to selected ones.

**Your own server:** **Admin › Installed connectors**, then declare a connector of type `mcp`. The
same by API, for a documentation search server your team runs:

```shell
curl -s "$CHOREGOS_API_URL/api/v1/orgs/acme/connectors" -H "Authorization: Bearer $CHOREGOS_TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "name": "docs-search",
  "type": "mcp",
  "config": { "url": "https://mcp.docs.example.com/mcp" },
  "secret_values": { "token": "the server key" },
  "availability": "selected_projects",
  "allow_egress": true
}'
```

- The key is write-only: it is sealed and never shown again. It is sent as `Authorization: Bearer`
  (add `"auth_scheme": "basic"` and a `username` for Basic).
- `allow_egress: true` opens the server's host in the organisation's network list in the same call;
  without it, a host that is not open answers `409 egress_required`, and the console shows the
  *Network access needed* panel (**Allow and continue** for an administrator, **Ask an administrator**
  for anyone else).
- `selected_projects` makes the server available to no project until you turn it on for one
  (**Projects…** on the server, or the project's settings).

## 2. Discover its tools, then open them

Choose **Discover** on the server (**Admin › Installed connectors**). Choregos lists the tools the
server offers. **A new tool is born closed** (`forbidden`): a server never adds a capability behind
your back. A tool whose input schema changes later is closed again.

Open the ones you want: set each tool's policy on the server's line. A tool annotated read-only by the
server is a read; any other is a write. Open reads as `allowed`, and writes as `approval` unless you
are sure. The same by API:

```bash
curl -s -X POST "$CHOREGOS_API_URL/api/v1/orgs/acme/connectors/docs-search/discover" \
  -H "Authorization: Bearer $CHOREGOS_TOKEN"
curl -s -X PATCH "$CHOREGOS_API_URL/api/v1/orgs/acme/connectors/docs-search/operations/search_docs" \
  -H "Authorization: Bearer $CHOREGOS_TOKEN" -H 'Content-Type: application/json' -d '{"policy": "allowed"}'
```

**What you should see:** the discovery lists the tools it `added`, each with its policy `forbidden`
until you open it.

A **write is a governed action** even when it is `allowed`: it is recorded under its own key, done
once even if the agent repeats the call, and journaled with its result. With `approval`, a person
approves it in the console first.

## 3. Grant the tools to an agent

In the agent's page, section **Tools (MCP servers)**, or in its spec:

```json
{
  "mcp_servers": [
    { "connector": "docs-search", "tools": ["search_*", "get_page"] }
  ]
}
```

`["*"]` takes every open tool of the server; a server named with no tool opens nothing. A grant may
also bind a discovered tool's arguments with a JSON Schema, for instance to the run's own project:

```json
{
  "mcp_servers": [
    {
      "connector": "docs-search",
      "tools": [{ "name": "search", "arguments": { "properties": { "space": { "const": "{project}" } }, "required": ["space"] } }]
    }
  ]
}
```

The run sees the tools as `docs-search__search_docs`. Publish the agent's next version
([Build an agent](agents.md#3-publish-the-next-version)).

**What you should see:** in a run's journal, each tool call with its arguments, its cost and the
answer; on the agent's **Access** tab, what the version reaches in a given project.

## Network

A run's network is closed unless something opens it. Three layers decide, each narrowing the one
above:

1. **The organisation's network list** (**Admin › Network**): the hosts the platform may call at all.
   Anyone can ask for a host; an administrator approves or refuses. Your operator can also set a
   ceiling that no organisation goes beyond.
2. **The project's policy** (**Settings › Guardrails › Network**): the hosts this project's runs may
   reach, and those they never may.
3. **The agent's version** (`network.domains`): what this agent needs, which can only narrow the
   project's.

The project's part of its policy, for a project whose implementer installs Python packages:

```yaml
apiVersion: choregos/v1
kind: Policy
metadata: { name: billing-api, version: 1, preset: team }
budgets:
  ticket_usd: { default: 8 }
  tool_calls_per_run: 20
sandbox:
  prompt_injection: block
  network:
    allow_domains: [pypi.org, files.pythonhosted.org, docs.example.com]
    deny_domains: [pastebin.com]
    builtin_web_tools: deny
```

`example.com` covers the host and its subdomains, `*.example.com` the subdomains only; no scheme, port,
path or IP address. A deny always wins. `builtin_web_tools: deny` turns off the coding agent's own web
tools. `prompt_injection: block` withholds a tool answer that looks like an injection (`warn` journals
it). `tool_calls_per_run` caps the calls of one run.

**What you should see:** in **Admin › Network**, card *Network activity*, each connection a run opened or was refused,
with the layer that decided.

## What can go wrong

| Answer to a tool call | Why | Fix |
| --- | --- | --- |
| `404` | the tool is not in the agent's version, is `forbidden`, waits for approval, or the server is not on for the project | grant it, open it, or turn the server on for the project |
| `400` | arguments the tool's schema refuses, or outside the grant (`arguments_outside_grant`) | fix the call or widen the grant |
| `429` | the run reached `tool_calls_per_run` | raise it in the project's policy |
| `451` | the answer was withheld by the injection guard (`block`) | read the journal; the server returned suspicious text |
| `403 network_policy` | the server's host is not allowed for the project | allow it in the project's network |
| `409 egress_required` when adding | the host is not in the organisation's list | an administrator allows it |
| `502` | the connector's key cannot be resolved, or the server failed | check the connector's state on its page |

## Go further

- [Write a skill](skills.md): knowledge, where a tool is an action.
- Bringing an organisation's own MCP gateway:
  [the gateway reference](../../integrations.md#your-organisations-mcp-gateway-adr-0105).
- Tools in a network the platform cannot reach, for an organisation with no gateway there:
  [outposts](../../outposts.md). They are off unless your operator turns them on; a write
  through one is delivered at most once, and may end in doubt, which a person settles on the
  action's page once they have checked the system it was for.
- [Host: security model](../host/security.md), for what the sandbox and the egress proxy enforce.
