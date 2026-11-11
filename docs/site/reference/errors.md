---
title: Error codes
description: Every answer the A2A door, the MCP door, the HTTP triggers and the REST API can refuse with, what it means, and what to do.
sidebar_position: 3
---

# Error codes

What each door answers when it refuses, and what to do about it. The A2A codes below are checked
against the door's code in the repository's tests: a code the door gains or loses without this page
turns the build red.

## The A2A door

**HTTP answers**, before the JSON-RPC call is read. The body is `{"error": "<message>"}`; a `401` or
`403` also carries a `WWW-Authenticate: Bearer` header.

| HTTP | Means | What to do |
| --- | --- | --- |
| `401` | no token, or an unknown, revoked, expired token, or an application that was removed | send a live token with the `a2a` scope |
| `403`, `insufficient_scope` | a token without the `a2a` scope (a full-access token included) | create an `a2a` token, or an application's token |
| `403`, bound to a project | the token is bound to another project | use that project's token |
| `403`, origin refused | a web page called the door | call it from a server, not a browser |
| `404` | no such organisation, or none the token can see | check the organisation in the URL |
| `413` | the message is larger than the door or the project takes | send smaller files |
| `429` | more than 120 calls a minute on this token | wait for `Retry-After` |

**JSON-RPC errors**, in an HTTP `200` (a `400` for `-32700` and `-32600`). In A2A 1.0, `error.data` is a
list whose first element is a `google.rpc.ErrorInfo`; refused fields come in a `google.rpc.BadRequest`.

| Code | Name | When | What to do |
| --- | --- | --- | --- |
| `-32700` | parse error | the body is not JSON | send JSON |
| `-32600` | invalid request | not a JSON-RPC 2.0 object, a batch, no `method` | send one JSON-RPC object |
| `-32601` | method not found | a method neither 0.3.0 nor 1.0 has, or an `A2A-Version` header that disagrees with the method | use one version's names |
| `-32602` | invalid params | no or unknown skill, a missing `message`, a bad `messageId`, too many parts, a file not in base64 or too large, a file by `uri`, fields the workflow refuses (`error.data.errors`), no right to open work items | read the message; it names the field |
| `-32603` | internal error | a file could not be stored; nothing was created | send again |
| `-32001` | task not found | an unknown task id, or one this token cannot see; a stream whose token stopped working | check the id and the token |
| `-32002` | task not cancelable | the task is over, its workflow is not running, or you may not stop work items | nothing to cancel |
| `-32003` | push notifications not supported | push needs a `chg_` token, and the instance's secrets key | use a token, ask your operator |
| `-32004` | unsupported operation | `ListTasks`, or a reply to a task: a decision is taken in the console, at `decision_url` | keep task ids; decide in the console |
| `-32005` | content type not supported | the skill does not take this media type | add it to the workflow's `a2a.input_modes` |
| `-32007` | extended card not configured | `GetExtendedAgentCard`: the card is already read with a token | read the card |
| `-32009` | version not supported | an `A2A-Version` other than `0.3` and `1.0` | send `0.3`, `1.0`, or no header |
| `-32050` | budget exhausted | the daily write budget: 50 for a person, with the MCP door; an application's own (500 by default) | wait, or raise the application's budget |
| `-32051` | too many streams | a sixth stream open on one token | close one |

## The MCP door

| Answer | Means | What to do |
| --- | --- | --- |
| HTTP `400`, unsupported protocol version | the client speaks a version other than `2025-11-25`, `2025-06-18`, `2025-03-26` | update the client |
| HTTP `401` | no token, or an unknown, revoked, expired one; with OAuth, a token of the wrong audience or issuer | mint a token, or sign in again |
| HTTP `403`, `insufficient_scope` | a full-access token, or OAuth without an MCP scope | mint an `mcp:read` or `mcp:write` token |
| HTTP `403`, *sign in to the console once* | OAuth: the identity provider knows the person, Choregos does not yet | sign in to the console |
| HTTP `403`, bound to a project | the token belongs to another project | use the project's door or token |
| HTTP `404` | the project in `/mcp/projects/…` does not exist | check `org:slug` |
| HTTP `405` | a `GET` or `DELETE`: the door has no stream and no session | nothing: the protocol allows it |
| HTTP `413` | a message over 1 MiB | send less |
| HTTP `429` | more than 120 calls a minute on this token | wait for `Retry-After` |
| `-32601` | an unknown method | |
| `-32602`, *Unknown tool* | a tool that does not exist, or that your token or role may not use | use a token with `mcp:write`, or ask for the right |
| a tool result with `isError`, `budget_exhausted` | 50 writes a day through the MCP and A2A doors | wait |
| a tool result with `isError`, other text | a missing `project`, an unknown work item, no right to open one, invalid arguments | read the message |
| `[result_truncated: …]` at the end of a result | the result was longer than 60,000 characters | narrow the search |

## HTTP triggers

| HTTP | Means |
| --- | --- |
| `202` | accepted: `outcome` is `created_work_item`, `already_open`, `reopened` or `ignored`; `duplicate: true` for a delivery received before |
| `401` | an invalid trigger token, or a missing or wrong `X-Choregos-Signature` |
| `404` | no such organisation, project or trigger, or a trigger without a token yet |
| `409` | the project's tracker is external |
| `413` | a body over 256 KiB |
| `422` | not a JSON object, no text or number at the `key` pointer, or fields the workflow refuses |
| `429` | too many calls from one address (600 a minute by default) |

See [HTTP triggers](../integrate/triggers.md).

## The REST API

Errors are RFC 9457 problems (`application/problem+json`): `type`, `title`, `status`, and often
`detail`, `errors` (each with `loc`, `msg` and sometimes `code`, `line`, `column`) and `code`.

| Status | Means |
| --- | --- |
| `400` | the request breaks a rule of the platform; `title` names it |
| `401` | `unknown or revoked API token`, `expired API token`, or no session |
| `403` | the token is for the MCP or A2A door (the REST API needs the `*` scope), the role lacks the right, or a change with the session cookie from another origin |
| `404` | not found, or not visible to you; an ambiguous bare project slug asks for `org/slug` |
| `409` | a conflict; `code` says which ([below](#conflict-codes)) |
| `422` | the body does not match the contract (`Invalid request`), or a document the platform refuses (`errors` lists each problem) |
| `429` | too many sign-in or webhook calls from one address |
| `500` | an internal error: give the `correlation_id` and the `X-Request-Id` to your operator |
| `502` | an external system (a tracker, a model gateway, an MCP server) failed; `retry_after` when it may pass |

### Conflict codes

| `code` | Means | What to do |
| --- | --- | --- |
| `stale_base` | someone published another version since the one you read (`latest_version` or `latest_revision` says which) | read it, apply your change again |
| `no_change` | the new version is the same as the current one | nothing |
| `eval_failed` | the agent's evals failed on the new version | fix the version, or the eval |
| `egress_required` | a host the change needs is not in the organisation's network list | an administrator allows it, or passes `allow_egress: true` |
| `gateway_required` | the organisation requires its MCP gateway; a direct MCP server is refused | add the server through the gateway |
| `gateway_not_configured` | the server is to go through a gateway that is not set up | set up the gateway |
| `gateway_in_use` | removing a gateway that connectors still use | move them first |

### Workflow validation

When a workflow is refused, `errors` lists each problem with its `code`, `line` and `column`:

| Code | Means |
| --- | --- |
| `yaml.syntax`, `yaml.not_a_mapping` | the file is not a YAML mapping |
| `schema.json` | a key the schema does not know, or a wrong type |
| `workflow.initial_state_unknown`, `workflow.initial_state_terminal` | `initial` names no state, or a terminal one |
| `workflow.no_terminal_state`, `workflow.terminal_unreachable` | the work item could never end |
| `workflow.no_human_state` | no state waits for a person |
| `workflow.no_verify_before_pr` | a pull request is opened without a verification step |
| `workflow.implicit_effect` | an effect deduced from a state's name: write `does` or `production` |
| `workflow.name_reserved` | a reserved workflow name |
| `state.unknown`, `state.orphan`, `state.no_inbound` | a state that is not declared, or that nothing leads to |
| `actor.unknown`, `actor.unused` | an actor that is not declared, or never used |
| `actor.group_empty`, `actor.group_placeholder`, `actor.group_unknown` | a human actor's group that is empty, a placeholder or unknown |
| `transition.unbounded_retry` | a step that goes back without `on_fail.max_attempts` |
| `transition.does_requires_system` | `does` on a transition whose actor is not `system` |
| `transition.pull_request_without_open_pr` | `pull_request` on a transition that does not open one |
| `gate.unknown`, `gate.nothing_to_check`, `gate.output_schema`, `gate.already_fixed_not_an_exit` | a gate that does not exist, has nothing to check, or is misused |
| `action.not_system`, `action.on_fail_missing`, `action.effect_unknown`, `action.date_field_unknown`, `action.date_field_not_date` | a governed action declared on the wrong actor, without a way out, or with an unknown effect or date field |
| `decision.output_collision`, `decision.threshold_without_human`, `decision.gateway_unsupported`, `decision.model_usage` | a decision step whose outputs collide, without a person behind a threshold, or a gateway or model that cannot decide |
| `policy.*` | a policy setting the platform does not enforce (a warning) |

Other codes you may meet: `skill.slug_reserved` (a reserved skill name), `agent_incomplete` (an agent
version missing what a step needs).
