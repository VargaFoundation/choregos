---
title: Build an agent
description: From nothing to a published, governed agent used in a workflow, as a prompt agent, a coding agent, or a Python program with the agent SDK.
sidebar_position: 2
---

# Build an agent

An agent is an entry of your organisation's **agent registry**: a name, and versions that say what it
is told (instructions), what it runs on (a model, a backend or your own code), what it may use
(skills, tools, knowledge, network) and what it may spend (limits and budget). A workflow step names
the agent; the platform runs it in a sandbox, journals every call, and checks its result before the
work item moves on.

**What you will be able to do:** create an agent, publish it, use it in a workflow, publish its next
version, and, for a program of your own, write it in Python with the agent SDK and ship it as an image.

**Before you start:**

- the right to manage your organisation's agents (an organisation administrator);
- a project with a workflow you can publish (here `acme:billing-api`);
- for an agent written in code, an operator who can add an image to the platform.

## Which agent do you need?

| You have | Build | Runs |
| --- | --- | --- |
| a judgement that instructions and a model can carry (write release notes, triage a ticket) | a **prompt agent**: instructions and a model, run by the platform's own tool loop | a workflow step |
| work in a repository (implement a change, fix a test) | a **coding agent**: instructions and a coding-agent backend such as `claude-code` | a workflow step, in a sandbox with the repository |
| a program of your own: a pipeline with a few model calls, a LangGraph graph, existing Python | a **code agent**, written with the Python agent SDK | a workflow step, in your image |
| an agent built for Azure AI Foundry's hosted contract | a **hosted agent**: your container, unchanged | a workflow step, a session |
| a spoken assistant | a **voice agent**: a realtime model and a voice | a session only |
| a fixed computation with no model | not an agent: a `job` step of the workflow ([reference](../reference/workflow-yaml.md#actors)) | a workflow step |

## Part 1: a prompt agent, from nothing to a workflow step

Here: an agent that drafts the release notes of a change, following the
[`release-notes-style` skill](skills.md).

### 1. Create the agent

**In the console:** sidebar **Agents**, **New agent**, then *Or a prompt agent*. Fill card *1. The
agent* (name, identifier, instructions, model) and card *2. How it answers* (text or a JSON object, and
how it uses tools), then **Publish the agent**. Skills, tools and knowledge are added afterwards on the
agent's page (sections **Skills**, **Tools (MCP servers)**, **Knowledge**).

**By API**, the same agent in one call:

```shell
curl -s "$CHOREGOS_API_URL/api/v1/orgs/acme/agents" -H "Authorization: Bearer $CHOREGOS_TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "slug": "release-notes",
  "display_name": "Release notes writer",
  "description": "Drafts the release notes of a change from its ticket.",
  "spec": {
    "instructions": "Write the release notes of {{ ticket.title }}. Follow the release-notes-style skill. Write them to release_notes.",
    "model": "profile:standard",
    "runtime": { "kind": "prompt" },
    "limits": { "max_turns": 20, "max_minutes": 10 },
    "budget": { "run_usd": 0.5, "daily_usd": 5.0 },
    "skills": [{ "slug": "release-notes-style" }]
  }
}'
```

| In `spec` | What it says |
| --- | --- |
| `instructions` | what the agent is told; `{{ ticket.title }}` and the like are filled from the work item |
| `model` | a profile of your instance (`profile:standard`, `profile:strong`, `profile:cheap`) or a model id |
| `runtime` | `{"kind": "prompt"}` here; absent for a coding agent, which names a `backend` instead |
| `limits` | `max_turns` (model and tool calls) and `max_minutes` per run |
| `budget` | what a run, a day and a month may spend, in USD |
| `skills` | the skills it carries ([Write a skill](skills.md)) |
| `mcp_servers` | the tools it may call, by server and tool name ([Tools](tools.md)) |
| `knowledge` | the knowledge bases it reads |
| `network` | hosts it needs, which can only narrow what the project allows |

**What you should see:** HTTP `201`, the agent at version 1, `active`. Use `"lifecycle": "draft"` to
keep it out of workflows until you activate it.

**A coding agent** is the same entry with a backend instead of a runtime, for steps that change the
repository:

```json
{
  "slug": "implementer",
  "display_name": "Implementer",
  "spec": {
    "instructions": "Implement the approved specification. Keep the change inside the allowed paths and add tests.",
    "backend": "claude-code",
    "model": "profile:strong",
    "limits": { "max_turns": 120, "max_minutes": 60 },
    "budget": { "run_usd": 5.0, "daily_usd": 50.0 }
  }
}
```

### 2. Use it in a workflow

A step names the agent with `agent:`. Here a short workflow: the agent drafts, a person approves.

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: release-notes
  version: 1
  description: An agent drafts the release notes of a change, a person approves them.
  inputs:
    type: object
    required: [ticket]
    properties:
      ticket: { type: string }
actors:
  writer: { type: agent, role: release_notes, agent: release-notes }
  owner: { type: human, group: product-owners, sla_hours: 24 }
states:
  inbox: { display: To write, kind: wait }
  review: { display: To approve, kind: wait }
  published: { display: Published, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  dropped: { display: Dropped, terminal: true, outcome: cancelled }
transitions:
  - id: t-draft
    from: inbox
    to: review
    by: writer
    outputs: [release_notes]
    gates: [outputs_present]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-approve
    from: review
    to: published
    by: owner
    on_reject: inbox
  - id: t-drop
    from: needs_human
    to: dropped
    by: owner
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
  needs_human:
    on_answer: resume
    on_abandon: dropped
```

The gate `outputs_present` checks that the agent really wrote `release_notes` before the work item
moves on; `on_fail` retries twice, then hands it to a person. Publish it:

```bash
choregos workflow validate release-notes.yaml
choregos workflow push acme:billing-api release-notes.yaml
```

`agent: release-notes` runs the project's pinned version of the agent, or else its latest; write
`agent: release-notes@1` to pin a version in the workflow itself. A project pins a version for all its
workflows in its **Agents** page.

**What you should see:** open a work item in this workflow, from the console's board or with the CLI:

```bash
choregos items create acme:billing-api --workflow release-notes --title "Release 2.4" --field ticket=FIN-42
```

The run appears on the work item with its model, its cost and its journal, and the
work item stops at *To approve* with the draft attached.

### 3. Publish the next version

Every change is a new version; the running work items finish on theirs. Change the definition on the
agent's page and publish it with a message, or by API, giving the version you read so that a
concurrent change is not overwritten:

```shell
curl -s "$CHOREGOS_API_URL/api/v1/orgs/acme/agents/release-notes/versions/publish" \
  -H "Authorization: Bearer $CHOREGOS_TOKEN" -H 'Content-Type: application/json' -d '{
  "message": "Breaking changes first",
  "base_version": 1,
  "spec": {
    "instructions": "Write the release notes of {{ ticket.title }}. Breaking changes first. Follow the release-notes-style skill. Write them to release_notes.",
    "model": "profile:standard",
    "runtime": { "kind": "prompt" },
    "limits": { "max_turns": 20, "max_minutes": 10 },
    "budget": { "run_usd": 0.5, "daily_usd": 5.0 },
    "skills": [{ "slug": "release-notes-style" }]
  }
}'
```

A `409` with `code: stale_base` means someone published in between: read the latest version and try
again; `no_change` means the spec is the same. The agent's **History** shows each version, and an older
one can be restored as the next.

## Part 2: an agent written in Python, with the agent SDK

When the behaviour is a program rather than instructions, write it in Python. `choregos-agent-sdk` is
not a framework: it connects a program, written with any framework or none, to the platform's
guarantees. The runner starts your program, every tool call and every file it writes asks the runner's
permission first, every model call counts against the step's `max_turns` and the run's budget, and its
result is checked like any agent's.

### 1. Write it

```shell
pip install ./choregos_agent_sdk-0.26.1-py3-none-any.whl ./choregos_contracts-0.26.1-py3-none-any.whl
```

The SDK's wheel and the contracts it validates against are attached to each
[GitHub release](https://github.com/VargaFoundation/choregos/releases); take the version of your
instance.

```python
from choregos_agent_sdk import Context, Result, agent


@agent(name="ticket-triager")
async def run(ctx: Context) -> Result:
    ticket = ctx.task  # the work item, its inputs and the expected outputs, as text
    tools = await ctx.tools()  # the run's tools, each call checked by the runner
    context = ""
    if await tools.has("knowledge_search"):
        found = await tools.call("knowledge_search", {"query": ticket[:200]})
        context = found.text
    reply = await ctx.llm.chat(
        [
            {"role": "system", "content": "Classify the ticket: bug, feature or question. Then explain why."},
            {"role": "user", "content": f"{ticket}\n\nWhat the team already knows:\n{context}"},
        ]
    )
    await ctx.write_output("triage.md", reply.text)  # stored as an artefact of the step
    return Result(summary="triaged", facts={"has_context": bool(context)})


if __name__ == "__main__":
    run.serve()
```

| In `ctx` | What it is |
| --- | --- |
| `ctx.task`, `ctx.fields`, `ctx.inputs` | the task text, the work item's fields, the declared inputs as files |
| `ctx.llm.chat(messages)` | one model call through the instance's gateway, counted as a turn; `ctx.llm.openai_client()` gives a client for a framework |
| `await ctx.tools()` | the run's tools: `names()`, `has(name)`, `call(name, args)` |
| `ctx.write_output(name, content)` | a deliverable, once the runner allowed it |
| `ctx.say(text)` | a line in the run's journal |

`Result(summary, facts={...})` is the step's result; `facts` are plain values a gate can read. An
exception ends the step `failed` (`agent_error`), a refused permission `permission_denied`, the turn
limit `max_turns`.

Use `tools.call`, never the run's MCP addresses with another client, and give a framework
`ctx.llm.openai_client()` or `ctx.llm.http_client()` rather than a client built from the URL and key:
both bypasses work, and both skip the runner's checks and the turn count. LangGraph, the OpenAI Agents
SDK and Pydantic AI have bridges in the SDK; the
[code agents reference](../../code-agents.md#bring-your-framework)
shows each.

### 2. Ship it as an image

1. Build an image **from the runner image** of your instance's version, with your agent in its own
   virtualenv (example:
   [`examples/agents/plain/Dockerfile`](../../../examples/agents/plain/Dockerfile)).
2. Push it and give its digest to your operator, who names it in the platform's agent images
   (`global.images.agents` in the Helm values). An image is never named by a workflow or an agent, only
   by the operator, pinned by digest.
3. Register the agent with a `code` runtime: in the console, the agent builder's **Runtime** selector,
   *Code (image)*; or in the spec:

```json
{
  "slug": "ticket-triager",
  "display_name": "Ticket triager",
  "spec": {
    "model": "profile:standard",
    "runtime": {
      "kind": "code",
      "image": "ticket-triager",
      "command": ["/opt/agents/triager/bin/python", "-m", "ticket_triager"],
      "api_format": "openai"
    },
    "limits": { "max_turns": 10, "max_minutes": 5 }
  }
}
```

4. Use it in a workflow like any agent: `triager: { type: agent, role: triage, agent: ticket-triager }`.

An unknown image name is refused when the version is published; the error lists the names your
operator gave. The platform's conformance suite checks a code agent the same way as a shipped backend
(`make conformance-agent` in the repository).

## Voice and hosted agents

- A **voice agent** is a prompt agent that speaks: a realtime model, a voice, a greeting, the longest
  call. It runs in sessions, never as a workflow step. Console: **New agent**, *Or a voice agent*.
- A **hosted agent** is your own container serving Azure AI Foundry's hosted contract on port 8088
  (`responses` or `invocations`). Give an image reference (from a registry your operator allows; its
  digest is resolved and pinned at publication) or upload a zip the platform builds. The runner gives
  the container the run's model, its tools and the network on loopback addresses, never a credential.
  Console: **New agent**, *Or a hosted agent (image or zip)*. See the
  [hosted agents reference](../../hosted-agents.md).

## What can go wrong

| Symptom | Why | Fix |
| --- | --- | --- |
| `422` when creating | a key the API does not know, a `backend` with a `prompt` runtime, a reserved slug (`new`) | read `errors`: each names the key |
| the workflow is refused on publication | the actor names an agent that does not exist, or names both `agent:` and a `backend:` the agent does not allow | create the agent first; let the agent decide its backend |
| the step fails `max_turns` | the agent needed more calls than `limits.max_turns` | raise the limit in a new version, or narrow the task |
| the step fails `budget` | the run spent `budget.run_usd`, or the day's budget is spent | raise it in a new version, or wait for the next day |
| a tool call answers `404` | the agent's version does not grant the tool, or the tool is closed | grant it ([Tools](tools.md)) |
| the step stops with a host refused | the project's network does not allow a host the agent needs | allow it in the project ([Tools](tools.md#network)) |

## Go further

- [Tools and network](tools.md): what an agent may call and reach.
- [Package agents as a plugin](plugins.md), to install them in other organisations.
- [Workflow YAML reference](../reference/workflow-yaml.md): every key of a step.
