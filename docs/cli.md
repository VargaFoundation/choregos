# CLI reference

Generated from the CLI itself by `tools/gen_cli_reference.py` — do not edit by hand
(`make docs-cli` regenerates it, a test checks it is current).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` overrides the
path): `api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`,
`CHOREGOS_TOKEN` and `CHOREGOS_ORG` are read when no profile exists.


## `choregos admin`

Platform administration

## `choregos admin secrets`

Sealed connector secrets

### `choregos admin secrets rewrap`

```text
Usage: choregos admin secrets rewrap [OPTIONS]

  Re-wrap every sealed secret under the active key, after a key rotation (the
  new key first in CHOREGOS_SECRETS_KEKS, the API restarted). The values are
  not read out nor changed.

Options:
  --help  Show this message and exit.
```

## `choregos dev`

Development environment

### `choregos dev demo`

```text
Usage: choregos dev demo [OPTIONS]

  Play the offline demonstration (no service needed).

Options:
  --help  Show this message and exit.
```

### `choregos dev down`

```text
Usage: choregos dev down [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos dev seed`

```text
Usage: choregos dev seed [OPTIONS]

  Seed the local environment: organisation, demo project, work items.

Options:
  --help  Show this message and exit.
```

### `choregos dev up`

```text
Usage: choregos dev up [OPTIONS]

  Bring the development environment up (kind + Tilt).

Options:
  --help  Show this message and exit.
```

## `choregos findings`

Findings

### `choregos findings action`

```text
Usage: choregos findings action [OPTIONS] {finding_id} {action}

Arguments:
  finding_id  [required]
  action      create_ticket|mark_duplicate|dismiss|agent_ready  [required]

Options:
  --help  Show this message and exit.
```

### `choregos findings list`

```text
Usage: choregos findings list [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --status <str>
  --help          Show this message and exit.
```

## `choregos items`

Work items

### `choregos items action`

```text
Usage: choregos items action [OPTIONS] {item_id} {action}

Arguments:
  item_id  [required]
  action   pause|resume|stop|rerun_stage|mark_agent_ready  [required]

Options:
  --top-up <float>  rerun_stage: USD granted on top of a spent budget
                    (audited)
  --help            Show this message and exit.
```

### `choregos items answer`

```text
Usage: choregos items answer [OPTIONS] {item_id} {answer}

Arguments:
  item_id  [required]
  answer   [required]

Options:
  --help  Show this message and exit.
```

### `choregos items approve`

```text
Usage: choregos items approve [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --reason <str>
  --help          Show this message and exit.
```

### `choregos items create`

```text
Usage: choregos items create [OPTIONS] {project}

  File a request in Choregos (a project with the internal tracker) and start
  its interpreter.

Arguments:
  project  [required]

Options:
  --title <str>     [required]
  --body <str>
  --size <str>      S, M, L or XL
  --no-start        file it without starting the interpreter
  --workflow <str>  the workflow the request is born in; otherwise the
                    project's routing, then its default
  --label <str>     a label (repeatable), read by the routing
  --field <str>     `key=value` (repeatable), validated by the workflow's
                    `inputs`
  --help            Show this message and exit.
```

### `choregos items list`

```text
Usage: choregos items list [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --state <str>
  --help         Show this message and exit.
```

### `choregos items reject`

```text
Usage: choregos items reject [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --reason <str>  [required]
  --help          Show this message and exit.
```

### `choregos items show`

```text
Usage: choregos items show [OPTIONS] {item_id}

  A work item in detail: state, costs, timeline.

Arguments:
  item_id  [required]

Options:
  --help  Show this message and exit.
```

## `choregos knowledge`

Knowledge bases as code

### `choregos knowledge apply`

```text
Usage: choregos knowledge apply [OPTIONS]

  Make a knowledge base match its manifest: create or patch the base, its
  sources, its uploads and its eval set. Idempotent: a second run changes
  nothing.

Options:
  -f, --file <path>  The manifest (YAML).  [required]
  --org <str>        The organisation (default: the profile's).
  --dry-run          Print the plan, change nothing.
  --prune            Also remove the sources and uploads the manifest does not
                     declare.
  --help             Show this message and exit.
```

### `choregos knowledge eval`

```text
Usage: choregos knowledge eval [OPTIONS] {slug}

  Run the base's eval set against its index and print recall@5.

Arguments:
  slug  [required]

Options:
  --org <str>
  --help       Show this message and exit.
```

### `choregos knowledge sync`

```text
Usage: choregos knowledge sync [OPTIONS] {slug}

  Sync every source of a base now.

Arguments:
  slug  [required]

Options:
  --org <str>
  --help       Show this message and exit.
```

### `choregos login`

```text
Usage: choregos login [OPTIONS]

  Save the connection profile (`~/.config/choregos/config.json`).

  The CLI cannot issue the first token (`tokens create` itself needs one): get
  it from the console, under `/admin` (API tokens), then pass it with
  `--token`.

Options:
  --api-url <str>  [default: http://localhost:8000]
  --token <str>
  --org <str>      Default organisation slug (else --org per command or
                   CHOREGOS_ORG)
  --help           Show this message and exit.
```

## `choregos orgs`

Organisations

### `choregos orgs create`

```text
Usage: choregos orgs create [OPTIONS] {slug}

  Create an organisation (requires being an admin of an organisation already).

Arguments:
  slug  [required]

Options:
  --name <str>
  --help        Show this message and exit.
```

### `choregos orgs list`

```text
Usage: choregos orgs list [OPTIONS]

Options:
  --help  Show this message and exit.
```

## `choregos projects`

Projects

### `choregos projects create`

```text
Usage: choregos projects create [OPTIONS] {slug}

  Create a project; `--template` then starts the provisioning.

  Without `--repo`, the project has no repository (ADR 0012): the agent works
  in an empty directory, and requests are filed with `choregos items create`.

Arguments:
  slug  [required]

Options:
  --repo <str>      URL of the application repository (optional: work without
                    code has none)
  --name <str>
  --template <str>
  --org <str>
  --language <str>  [default: python]
  --tracker <str>   `internal`: requests are filed in Choregos. Without
                    `--repo` the default is `internal`; with `--repo` it is
                    written only when given
  --help            Show this message and exit.
```

### `choregos projects list`

```text
Usage: choregos projects list [OPTIONS]

Options:
  --org <str>
  --help       Show this message and exit.
```

### `choregos projects provision`

```text
Usage: choregos projects provision [OPTIONS] {project}

  Start (or restart) the provisioning, and follow it live with `--follow`.

Arguments:
  project  [required]

Options:
  --follow
  --help    Show this message and exit.
```

### `choregos projects readiness`

```text
Usage: choregos projects readiness [OPTIONS] {project}

  What the project still lacks to run, and who can fix each item. Exits 1
  while something is to do.

Arguments:
  project  [required]

Options:
  --help  Show this message and exit.
```

## `choregos runs`

Runs

### `choregos runs diff`

```text
Usage: choregos runs diff [OPTIONS] {run_id}

Arguments:
  run_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos runs list`

```text
Usage: choregos runs list [OPTIONS] {item_id}

Arguments:
  item_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos runs tail`

```text
Usage: choregos runs tail [OPTIONS] {run_id}

  Follow a run's ACP journal live (SSE).

Arguments:
  run_id  [required]

Options:
  --help  Show this message and exit.
```

## `choregos tokens`

API tokens

### `choregos tokens create`

```text
Usage: choregos tokens create [OPTIONS]

  Issue an API token for the caller. The secret is shown HERE, once.

Options:
  --name <str>             [required]
  --expires-in-days <int>  [default: 90]
  --scope <str>            `*` (REST API and CLI, the default), `mcp:read` /
                           `mcp:write` (MCP gate), or `a2a` (A2A door)
  --project <str>          `org:slug`: limits an MCP or A2A token to one
                           project
  --help                   Show this message and exit.
```

### `choregos tokens list`

```text
Usage: choregos tokens list [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos tokens revoke`

```text
Usage: choregos tokens revoke [OPTIONS] {token_id}

Arguments:
  token_id  [required]

Options:
  --help  Show this message and exit.
```

## `choregos trains`

Release trains

### `choregos trains approve`

```text
Usage: choregos trains approve [OPTIONS] {release_id}

Arguments:
  release_id  [required]

Options:
  --note <str>
  --help        Show this message and exit.
```

### `choregos trains depart`

```text
Usage: choregos trains depart [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos trains freeze`

```text
Usage: choregos trains freeze [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --reason <str>  [required]
  --env <str>     [default: prod]
  --help          Show this message and exit.
```

### `choregos trains status`

```text
Usage: choregos trains status [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos trains unfreeze`

```text
Usage: choregos trains unfreeze [OPTIONS] {project}

Arguments:
  project  [required]

Options:
  --env <str>  [default: prod]
  --help       Show this message and exit.
```

### `choregos whoami`

```text
Usage: choregos whoami [OPTIONS]

  Show the current user and their roles.

Options:
  --help  Show this message and exit.
```

## `choregos workflow`

Workflows

### `choregos workflow list`

```text
Usage: choregos workflow list [OPTIONS] {project}

  A project's workflows: the active version of each, the default, the open
  work items.

Arguments:
  project  [required]

Options:
  --help  Show this message and exit.
```

### `choregos workflow push`

```text
Usage: choregos workflow push [OPTIONS] {project} [path]

  Publish the next version of a workflow, under the name its `metadata.name`
  gives.

  The project's other workflows do not move, and a work item in progress
  finishes on its version.

Arguments:
  project  [required]
  path     [default: .choregos/workflow.yaml]

Options:
  --base-version <int>  the active version you read: 409 if another one
                        replaced it
  --help                Show this message and exit.
```

### `choregos workflow show`

```text
Usage: choregos workflow show [OPTIONS] [path]

  Show the workflow: states, transitions, a Mermaid diagram, or the process in
  plain words.

Arguments:
  path  [default: .choregos/workflow.yaml]

Options:
  --mermaid
  --process  each transition in plain words
  --help     Show this message and exit.
```

### `choregos workflow templates`

```text
Usage: choregos workflow templates [OPTIONS]

Options:
  --help  Show this message and exit.
```

### `choregos workflow validate`

```text
Usage: choregos workflow validate [OPTIONS] [path]

  Validate a workflow. Without `--remote`, the validation is local and
  offline.

Arguments:
  path  [default: .choregos/workflow.yaml]

Options:
  --remote  validate through the API rather than locally
  --help    Show this message and exit.
```
