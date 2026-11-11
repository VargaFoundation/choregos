---
title: Start a workflow over A2A
description: Let an application or another agent start one of your workflows, hand it data and files, follow it and read its result, over the A2A protocol.
sidebar_position: 2
---

# Start a workflow over A2A

Your billing team's order system should be able to say "ship this change" to Choregos and get the
result back, without anyone opening the console. The **A2A door** does that: it serves your workflows
as skills of the [A2A protocol](https://a2a-protocol.org), versions **0.3.0** and **1.0**, as JSON-RPC
over HTTP. Any HTTP client can call it; the CLI's `choregos a2a` writes the JSON-RPC for you.

**What you will be able to do:** open a workflow to the door, give an application its own account
and token, list what it may start, start a work item with fields and a file, follow it to the end,
read its artefacts, cancel it, and be called back when it changes.

**Before you start:**

- a project with a workflow you can publish (here `acme:billing-api` and its `ship-a-change` workflow);
- the right to manage the project's members, to create an application's account;
- the CLI, [installed](../cli/install.md) and signed in, if you want the CLI side of each step;
- `curl` and, for one step, `jq`.

Every request of this page is sent to the real door by the test suite, in the page's order: a request
the door stops taking turns the build red.

:::note What the door never does
It never takes a human decision. When a step waits for a person, the task says so and gives the link
where that person decides, in the console. An application's token opens the A2A door and nothing
else: not the REST API, not the MCP door.
:::

## 1. Open the workflow to the door

Nothing is served by default: the door answers with an empty card until a workflow says `a2a:`. Add
the block, and say in `metadata.inputs` which fields the workflow takes, as a JSON Schema:

```yaml
metadata:
  name: ship-a-change
  version: 1
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

| Key | What it says |
| --- | --- |
| `a2a.skill` | the skill's id; on the card it is `<project>/<skill>`, here `billing-api/ship-a-change` |
| `a2a.description` | what the caller's agent reads to choose the skill |
| `a2a.input_modes` | what the skill takes: `text/plain` for the request in words, `application/json` for the fields, a file's media type for that file; `[text/plain]` when absent |

Publish the workflow as usual, from the console (the workflow's **YAML** view) or with the CLI:

```bash
choregos workflow push acme:billing-api ship-a-change.yaml
```

The [workflow reference](../reference/workflow-yaml.md#a2a) lists every key.

## 2. Give the application its own account and token

An application should act under its own name: its account belongs to one project, keeps working when
the person who created it leaves, has its own daily budget (500 writes by default), and can never take
a decision. Its tokens open the A2A door only, on that project, and last 365 days at most.

**In the console:** open the project, then **Settings › People**, card **Applications**. Type the
application's name under *New application*, choose **Create**, then **Issue a token** on its line. The
token is shown once (*Token, shown once:*): copy it into the application's secret store.

**With the CLI:**

```bash
choregos machines create acme:billing-api partner-app --display-name "Partner app"
choregos machines token acme:billing-api partner-app --name production --expires-in-days 90
```

To rotate a token, issue the next one, switch the application to it, then revoke the old one with
`choregos machines revoke acme:billing-api partner-app <token id>` (`choregos machines list
acme:billing-api` shows the ids, never the values). **Remove** ends the application: its tokens stop
working at once.

**To try it as yourself first**, take a token that acts with your rights: the console's
**Integrations** page, card *Another agent, over A2A*, button **Create an A2A token** (30 days, shown
once), or:

```bash
choregos tokens create --name my-test --scope a2a --project acme:billing-api --expires-in-days 30
```

Keep both values at hand for the next steps:

```shell
export TOKEN=chg_...
export A2A=https://choregos.example.com/a2a/orgs/acme
```

The CLI finds the door from its profile (`choregos login`, or `CHOREGOS_API_URL` and `CHOREGOS_ORG`)
and takes the token from `--token` or `CHOREGOS_A2A_TOKEN`:

```shell
export CHOREGOS_API_URL=https://choregos.example.com CHOREGOS_ORG=acme CHOREGOS_A2A_TOKEN=chg_...
```

Without either, it sends the profile's own token, usually a full-access one, which the door refuses
with `403 insufficient_scope`; the CLI then tells you which token to create.

## 3. Read the card

The card lists the skills this token may start. It needs the token, since its content depends on who
asks.

```bash
curl -s "$A2A/.well-known/agent-card.json" -H "Authorization: Bearer $TOKEN"
```

A skill that takes `application/json` also says which fields it wants, as the JSON Schema of its
`metadata.inputs`. A2A has no place for that on a skill, so it is in an optional extension of the
card, which a client that does not know it simply ignores:

```shell
curl -s "$A2A/.well-known/agent-card.json" -H "Authorization: Bearer $TOKEN" | jq '.capabilities.extensions[]
  | select(.uri == "https://choregos.dev/a2a/extensions/skill-fields/v1") | .params.skills'
```

With the CLI, a required field is marked with a `*`:

```bash
choregos a2a skills
```

**What you should see:** one skill, `billing-api/ship-a-change`, taking `ticket` (required) and
`priority`. An empty list means no workflow of a project this token can read has opted in.

## 4. Start the workflow

```shell
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

With the CLI:

```bash
choregos a2a send billing-api/ship-a-change --message-id order-2026-0042 --title "Add credit notes" \
  --text "Customers need a credit note when an invoice is refunded." \
  --field ticket=FIN-42 --field priority=high
```

- The **text** becomes the work item's description, the **data** part its fields, checked against
  `metadata.inputs`. One data part at most.
- **`messageId` is your idempotency key.** Send the same message again after a timeout and you get the
  same work item back; nothing starts twice. Derive it from your own record, as here. The CLI, without
  `--message-id`, uses a digest of the message, so the same command finds the same task.
- `metadata.skill` may be left out when the card has a single skill.

**What you should see:** the task, in state `working`. Keep its `id`:

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

## 5. Send a file with it

A `file` part carries its bytes in base64. Its `mimeType` must be one of the skill's `input_modes`; the
first step that lists the file's `name` in its `inputs` finds it in `.choregos/inputs/`.

```shell
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

With the CLI, `--file` reads the file and guesses its media type from the extension
(`PATH;type=MEDIA/TYPE` names it otherwise):

```bash
choregos a2a send billing-api/ship-a-change --message-id order-2026-0043 \
  --title "Apply the attached specification" --field ticket=FIN-43 --file spec.pdf
```

A message carries 10 MiB of files by default, 50 MiB at most: the project sets it under
**Settings › Agents › A2A**. A file given by `uri` is refused: the door fetches nothing on a caller's
behalf.

## 6. Follow it

Read the task whenever you like:

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": { "id": "<task id>" }
}'
```

Or keep a connection open and receive each change as server-sent events, until a `final` status:

```shell
curl -sN "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 4, "method": "tasks/resubscribe", "params": { "id": "<task id>" }
}'
```

A stream lasts an hour at most, five at once per token: call it again to pick up where the task is.
`message/stream` takes the same `params` as `message/send` and starts and follows in one call.

With the CLI, `get` reads once and `follow` keeps the stream open, reopening it after the door's hour
cut; `send --wait` starts and follows in one command:

```bash
choregos a2a get <task id> --json
choregos a2a follow <task id> --out ./result
choregos a2a send billing-api/ship-a-change --text "Add a VAT column" --field ticket=FIN-44 --wait --out ./result
```

`follow` and `send --wait` exit `0` when the task completed, `3` when it failed, `4` when it was
canceled, `1` when the door refused a call or could not be reached: a script can branch on it.

| `status.state` | Means | What you do |
| --- | --- | --- |
| `working` | a step is running or queued | wait |
| `input-required` | a person must decide | send them the `decision_url` of the status message; the task goes on once they have decided in the console |
| `completed` | the workflow reached a final state | read `artifacts` |
| `failed` | the work item failed | open `metadata.choregos.console_url` |
| `canceled` | it was stopped or closed | nothing more will come |

`metadata.choregos.state` gives the workflow's own state, finer than these five.

## 7. Be called back instead

A server that would rather be called than hold a stream registers an `https` URL on the task. The
URL's host must be open in the organisation's network list (**Admin › Network**); with a strict list,
ask an administrator first.

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 5, "method": "tasks/pushNotificationConfig/set",
  "params": {
    "taskId": "<task id>",
    "pushNotificationConfig": { "id": "orders", "url": "https://hooks.partner.example/a2a", "token": "orders-check" }
  }
}'
```

The answer carries `signing.secret`, **shown once**. Each notification POSTs the task as it is now,
with:

- `X-A2A-Notification-Token`: your `token`, to check the call is yours;
- `X-Choregos-Event-Id`, also sent as `Idempotency-Key`: a receiver may see an event twice;
- `X-Choregos-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">`, computed with the
  secret: check it, and check that `t` is recent.

A receiver that does not answer 2xx is called again with a growing delay, eight times at most. The
registration ends with the task's final state, or when the token is revoked. It needs a `chg_` token:
a token obtained through OAuth cannot be used for it.

## 8. Read the result

A `completed` task carries the latest revision of each artefact in `artifacts`: markdown as text, a
JSON object as data, anything else as a file in base64. The content stops at 256 KiB per artefact and
1 MiB per task; beyond that, the part describes the artefact and the console serves it.

With the CLI, `--out <directory>` (on `get`, `follow` and `send --wait`) writes each artefact there:
text as `<name>.md` unless its name has an extension, a JSON object as `<name>.json`, a file under its
own name.

## 9. Cancel it

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 6, "method": "tasks/cancel", "params": { "id": "<task id>" }
}'
```

```bash
choregos a2a cancel <task id>
```

The work item stops as if someone had stopped it in the console. You need the right to control work
items in the project; an application's account has it.

## The same calls in A2A 1.0

The door speaks 1.0 at the same URL, with the same guards and limits. The method name says which
version a call is in; an `A2A-Version: 1.0` header is optional and, when sent, must agree. The same
`messageId` is the same task in either version.

| 0.3.0 | 1.0 |
| --- | --- |
| `message/send` | `SendMessage`, which answers `{"task": …}` |
| `message/stream`, `tasks/resubscribe` | `SendStreamingMessage`, `SubscribeToTask`: each event is `{"task"}`, `{"statusUpdate"}` or `{"artifactUpdate"}`, and the stream closes after the last status (no `final`) |
| `tasks/get`, `tasks/cancel` | `GetTask`, `CancelTask` |
| `tasks/pushNotificationConfig/set`, `get`, `list`, `delete` | `CreateTaskPushNotificationConfig`, `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig`, with flat params (`taskId`, `id`, `url`, `token`) |
| `"role": "user"` | `"role": "ROLE_USER"` |
| `{"kind": "text", "text": …}` | `{"text": …}` |
| `{"kind": "data", "data": {…}}` | `{"data": {…}}` |
| `{"kind": "file", "file": {"bytes", "mimeType", "name"}}` | `{"raw", "mediaType", "filename"}` |
| `working`, `input-required`, `completed` | `TASK_STATE_WORKING`, `TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_COMPLETED` |
| `error.data`: `{"errors": […], "decision_url": …}` | `error.data`: a `google.rpc.ErrorInfo` (its `metadata` holds `decision_url`), then a `google.rpc.BadRequest` whose `fieldViolations` name the refused fields |

Start, with a file:

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 11, "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER", "messageId": "order-2026-0045",
      "metadata": { "skill": "billing-api/ship-a-change", "title": "Credit notes in the customer portal" },
      "parts": [
        { "text": "Show credit notes next to their invoice in the customer portal." },
        { "data": { "ticket": "FIN-45", "priority": "low" } },
        { "raw": "JVBERi0xLjcK", "mediaType": "application/pdf", "filename": "mock-up.pdf" }
      ]
    }
  }
}'
```

Read, follow, be called back, cancel:

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 12, "method": "GetTask", "params": { "id": "<task id>" }
}'
```

```shell
curl -sN "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 13, "method": "SubscribeToTask", "params": { "id": "<task id>" }
}'
```

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 14, "method": "CreateTaskPushNotificationConfig",
  "params": { "taskId": "<task id>", "id": "orders", "url": "https://hooks.partner.example/a2a", "token": "orders-check" }
}'
```

```shell
curl -s "$A2A" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 15, "method": "CancelTask", "params": { "id": "<task id>" }
}'
```

`ListTasks` is not served (`-32004`): keep the ids `SendMessage` answers. There is no extended card
(`-32007`): the card is already read with a token.

## When it refuses

| Answer | Why | Fix |
| --- | --- | --- |
| HTTP `401` | no token, or an unknown, revoked or expired one | send `Authorization: Bearer chg_…` with a live token |
| HTTP `403`, `insufficient_scope` | the token has no `a2a` scope; a full-access token is refused too | create an `a2a` token, or an application's token |
| HTTP `403`, bound to a project | the token belongs to another project | use that project's token |
| HTTP `404` | no such organisation, or none this token can see | check the organisation in the URL |
| HTTP `413` | the message is larger than the door takes | send smaller files, or raise the project's limit |
| HTTP `429` | more than 120 calls a minute on this token | wait for `Retry-After` |
| `-32602`, `name the skill…` or `unknown skill` | several skills, or one this token cannot see | read the card, set `metadata.skill` |
| `-32602`, `fields refused by workflow …` | `metadata.inputs` refuses them; `error.data.errors` says which and why | send what the card's schema asks for |
| `-32601`, `unknown method` | a name neither version has, or an `A2A-Version` header that disagrees with the method | use one version's names, and its header or none |
| `-32009` | an `A2A-Version` other than `0.3` and `1.0` | send `0.3`, `1.0`, or no header |
| `-32005` | the skill does not take this media type | add it to `input_modes` |
| `-32004` | a reply to an existing task: a decision belongs in the console | follow `decision_url` |
| `-32050` | the daily budget: 50 writes for a person, shared with the MCP door; 500 by default for an application | wait, or give the application a larger budget |
| `-32051` | a sixth stream on the same token | close one, or poll with `tasks/get` |

Every code the door can answer is in [Error codes](../reference/errors.md#the-a2a-door).

## What is not there yet

- **No `ListTasks` and no signed card in A2A 1.0.** The 1.0 spelling is checked against the
  specification and the platform's own client, not yet against another vendor's 1.0 client.
- **The door cannot carry a decision.** A person decides in the console; the task then goes on.

## Go further

- [Open work items from any system that sends JSON](triggers.md), when the sender cannot speak A2A.
- [The REST API](rest.md), for your own scripts with a full-access token.
- The design decision behind the door:
  [ADR 0078](../../adr/0078-governed-workflows-as-a2a-skills.md).
