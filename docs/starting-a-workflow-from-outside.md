# Starting a workflow from outside

An application, a script, a monitoring tool or another agent can start a Choregos workflow and hand it
its data, without anyone opening the console. There are four ways in. This page says which one to take
and walks through the one that follows the work to its end.

`apps/api/tests/test_a2a_guide.py` replays the requests of this page against the door, in order: an
example that stops being true turns the suite red.

## Which door

| You are | Take | Credential | Gives you back |
| --- | --- | --- | --- |
| an application or an agent that wants the result | [**the A2A door**](#the-a2a-door-step-by-step) | an application's own token, or a person's, scope `a2a` | a task: its state, a stream or push notifications, the artefacts at the end |
| a terminal, a script, a CI job | [**the CLI or the REST API**](#from-a-terminal-or-a-script) | a person's token, scope `*` | the work item, then whatever you read of it |
| a system that can only send JSON: monitoring, a form, a CRM | [**an HTTP trigger**](#from-a-system-that-only-sends-json) | the trigger's own token, no person behind it | an acknowledgement and the work item's id; nothing follows |
| an assistant: Claude, Cursor, VS Code | [the MCP door](integrations.md) (`create_work_item`) | a person's token, scope `mcp:write`, or OAuth | tools the assistant calls and reads |

All four open a **work item** through the same path as the console, so the workflow's gates, budget and
journal apply the same way. None of them can take a human decision: when a step waits for a person, that
person decides in the console.

## The A2A door, step by step

The door speaks [A2A](https://a2a-protocol.org) **0.3.0**, JSON-RPC over HTTP, so any HTTP client can call
it. The rules behind each call are in the [reference](integrations.md#another-agent-over-a2a) and
[ADR 0078](adr/0078-governed-workflows-as-a2a-skills.md).

### 1. Open the workflow to the door

Nothing is served by default, and no template or catalogue workflow opts in: the door answers with an
empty card until you add `a2a:` to a workflow. Say what the workflow takes in `metadata.inputs`.

```yaml
metadata:
  inputs:
    type: object
    required: [ticket]
    properties:
      ticket: { type: string }
      priority: { type: string, enum: [low, high] }
a2a:
  skill: ship-a-change
  description: Ship a change through review and release.
  input_modes: [text/plain, application/json, application/pdf]
```

`input_modes` is what the skill accepts: `text/plain` for the request in words, `application/json` for
the fields, a file's media type for that file. Publish the workflow as usual:

```bash
choregos workflow push acme:billing-api ship-a-change.yaml
```

The skill is now `billing-api/ship-a-change`, for callers who may read that project.

### 2. Get a token

**For an application**, create its own account: it acts under its own name, on this project only, keeps
working when you leave, and never takes a decision. You need to be able to manage the project's members.

```bash
choregos machines create acme:billing-api partner-app
choregos machines token acme:billing-api partner-app --name production --expires-in-days 90
```

The console does the same under **Settings › People › Applications**. A token lasts 365 days at most. To
rotate one, issue the next, switch the application to it, then `choregos machines revoke` the old one.

**To try it yourself**, take a token that acts as you, from the console's **Integrations** page (*Create an
A2A token*, 30 days, shown once) or:

```bash
choregos tokens create --name my-test --scope a2a --project acme:billing-api --expires-in-days 30
```

`--project` keeps the token to that project. You must be able to control work items in the project.

```bash
export TOKEN=chg_...
export A2A=https://choregos.example.com/a2a/orgs/acme
```

### 3. See what you may start

```bash
curl -s "$A2A/.well-known/agent-card.json" -H "Authorization: Bearer $TOKEN"
```

The card's `skills` are the workflows this token may start. It needs the token because its content
depends on who asks. An empty list means no workflow of a project you can read has opted in.

A skill that takes `application/json` also says which fields it wants, as the JSON Schema of its
`metadata.inputs`. A2A has no place for that on a skill, so it is in an optional extension of the card:

```bash
curl -s "$A2A/.well-known/agent-card.json" -H "Authorization: Bearer $TOKEN" | jq '.capabilities.extensions[]
  | select(.uri == "https://choregos.dev/a2a/extensions/skill-fields/v1") | .params.skills'
```

A client that ignores the extension still reads the card.

### 4. Start the workflow

```bash
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "message/send",
  "params": {
    "message": {
      "kind": "message", "role": "user", "messageId": "order-2026-0042",
      "metadata": { "skill": "billing-api/ship-a-change", "title": "Add credit notes" },
      "parts": [
        { "kind": "text", "text": "Customers need a credit note when an invoice is refunded." },
        { "kind": "data", "data": { "ticket": "FIN-42", "priority": "high" } }
      ]
    }
  }
}'
```

- The **text** becomes the work item's body, the **data** part its fields, checked against
  `metadata.inputs`. One data part at most.
- **`messageId` is your idempotency key.** Send the same one again after a timeout and you get the same
  work item back, with nothing started twice. Derive it from your own record, as here.
- `metadata.skill` may be left out when the card has a single skill.

The answer is the task. Keep its `id`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "kind": "task",
    "id": "5f0c8a52-6c3e-5f0a-9d11-3a1c7be2f001",
    "contextId": "5f0c8a52-6c3e-5f0a-9d11-3a1c7be2f001",
    "status": { "state": "working" },
    "metadata": {
      "choregos": {
        "work_item": "5f0c8a52-6c3e-5f0a-9d11-3a1c7be2f001",
        "key": "BIL-12",
        "project": "acme:billing-api",
        "state": "inbox",
        "console_url": "https://choregos.example.com/p/billing-api/items/5f0c8a52-6c3e-5f0a-9d11-3a1c7be2f001"
      }
    }
  }
}
```

### 5. Send a file with it

A `file` part carries its bytes in base64. Its `mimeType` must be one of the skill's `input_modes`, and
the first step that lists its `name` in `inputs` finds it in `.choregos/inputs/`.

```bash
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 2, "method": "message/send",
  "params": {
    "message": {
      "kind": "message", "role": "user", "messageId": "order-2026-0043",
      "metadata": { "skill": "billing-api/ship-a-change", "title": "Apply the attached specification" },
      "parts": [
        { "kind": "data", "data": { "ticket": "FIN-43" } },
        { "kind": "file", "file": { "name": "spec.pdf", "mimeType": "application/pdf", "bytes": "JVBERi0xLjcK" } }
      ]
    }
  }
}'
```

10 MiB by default per message, 50 MiB at most (**Settings › A2A** of the project). A file given by `uri` is
refused: the door fetches nothing on a caller's behalf.

### 6. Follow it

Read it whenever you like:

```bash
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": { "id": "<task id>" }
}'
```

Or keep a connection open and receive each change as server-sent events, until a `final` status. A stream
lasts an hour at most, five per token; call it again to pick up where the task is.

```bash
curl -sN "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 4, "method": "tasks/resubscribe", "params": { "id": "<task id>" }
}'
```

`message/stream` takes the same `params` as `message/send`, and starts the work item and follows it in one
call. For a server that would rather be called back, see
[push notifications](integrations.md#another-agent-over-a2a).

| `result.status.state` | Means | What you do |
| --- | --- | --- |
| `working` | a step is running or queued | wait |
| `input-required` | a person must decide | send them `decision_url`, from the status message; the task goes on by itself once they have decided in the console |
| `completed` | the workflow reached a final state | read `artifacts` |
| `failed` | the work item failed | open `metadata.choregos.console_url` |
| `canceled` | it was stopped or closed | nothing more will come |

`metadata.choregos.state` gives the workflow's own state, finer than these five.

### 7. Read the result

A `completed` task carries the latest revision of each artefact in `artifacts`: markdown as text, a JSON
object as data, anything else as a file in base64. The content stops at 256 KiB per artefact and 1 MiB per
task; beyond that, the part describes the artefact and the console serves it.

### 8. Stop it

```bash
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 5, "method": "tasks/cancel", "params": { "id": "<task id>" }
}'
```

### When it refuses

| Answer | Why | Fix |
| --- | --- | --- |
| HTTP `401` | no token, or an unknown, revoked or expired one | check `Authorization: Bearer chg_…` |
| HTTP `403`, `insufficient_scope` | the token has no `a2a` scope; a `*` token is refused too | create an `a2a` token |
| HTTP `404` | no such organisation, or none this token can see | check the organisation slug in the URL |
| HTTP `429` | more than 120 calls a minute on this token | wait for `Retry-After` |
| `-32602` `name the skill…`, `unknown skill` | several skills, or one this token cannot see | read the card, set `metadata.skill` |
| `-32602` `fields refused by workflow …` | `metadata.inputs` refuses them; `error.data.errors` says which and why | send what the card's schema asks for |
| `-32601` `unknown method` | a method name from A2A 1.0, such as `SendMessage` | use the 0.3.0 names of this page |
| `-32005` | the skill does not list this media type | add it to `input_modes` |
| `-32004` | a reply to an existing task: a decision belongs in the console | follow `decision_url` |
| `-32050` | the daily budget: 50 writes for a person, shared with the MCP door; 500 by default for an application | wait, or give the application a larger budget |

## From a terminal or a script

The CLI and the REST API take a token of scope `*`, which the A2A door refuses, and the other way round.

```bash
export CHOREGOS_API_URL=https://choregos.example.com CHOREGOS_TOKEN=chg_... CHOREGOS_ORG=acme
choregos items create acme:billing-api --workflow ship-a-change \
  --title "Add credit notes" --body "Customers need a credit note when an invoice is refunded." \
  --field ticket=FIN-42 --field priority=high
choregos items list acme:billing-api
choregos items show BIL-12
```

Without `--workflow`, the project's routing picks one from the `--label`s, then its default applies. See
the [CLI reference](cli.md#choregos-items-create). A script reads the same thing over REST, and the answer
carries the `id` to poll:

```bash
curl -s "$CHOREGOS_API_URL/api/v1/projects/acme:billing-api/work-items" -H "Authorization: Bearer $CHOREGOS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "Add credit notes", "workflow": "ship-a-change", "fields": {"ticket": "FIN-42"}, "start": true}'
curl -s "$CHOREGOS_API_URL/api/v1/work-items/<id>" -H "Authorization: Bearer $CHOREGOS_TOKEN"
```

## From a system that only sends JSON

A **trigger** turns any JSON into a work item, with its own token and no person behind it: declare it in
the project, mint its token, hand over the URL. It maps the payload with JSON pointers, so the sender does
not have to know Choregos. It answers with the work item's id and reports nothing afterwards. The set-up is
in [Usage](usage.md#work-items-opened-by-another-system-http-triggers) and
[ADR 0113](adr/0113-a-workflow-starts-from-a-trigger.md).

## What is not there yet

- **A2A 0.3.0 only, inbound.** A client built only for 1.0, whose method names and part format differ,
  gets `-32601`. Outbound calls to remote agents already handle both.
- **An application's account opens the A2A door only.** The REST API and the MCP door take a person's token.
- **No CLI verb for the A2A door.** `choregos items create` goes through REST, and takes no file.
