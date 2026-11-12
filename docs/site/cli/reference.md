---
title: CLI reference
description: Every choregos command and option, generated from the CLI itself.
sidebar_position: 2
---

# CLI reference

This page is generated from the CLI itself, and the repository's tests fail when it differs from the
commands. `choregos <command> --help` prints the same text for the version you installed
([Install the CLI](install.md)).

The profile lives in `~/.config/choregos/config.json` (`CHOREGOS_CONFIG` names another file):
`api_url`, `token`, `org`. `choregos login` writes it; `CHOREGOS_API_URL`, `CHOREGOS_TOKEN` and
`CHOREGOS_ORG` are read when no profile exists.


## `choregos a2a`

Start and follow a workflow over the A2A door (an `a2a` token)

### `choregos a2a cancel`

```text
Usage: choregos a2a cancel [OPTIONS] {task}

  Stop a task: its work item is stopped, as from the console.

Arguments:
  task  the task id `send` printed  [required]

Options:
  --org <str>    the organisation; the profile's otherwise
  --token <str>  an `a2a` token; the profile's token otherwise, which the door
                 refuses when it is `*`  [env var: CHOREGOS_A2A_TOKEN]
  --json         print the task as the door answers it, for a script
  --help         Show this message and exit.
```

### `choregos a2a follow`

```text
Usage: choregos a2a follow [OPTIONS] {task}

  Follow a task to its end, over the stream, opened again when the door cuts
  it (an hour at most).

  Exit 0 completed, 3 failed, 4 canceled. A decision is printed with its URL;
  the task waits for it.

Arguments:
  task  the task id `send` printed  [required]

Options:
  --out <path>   write the artefacts of a completed task in this directory
  --org <str>    the organisation; the profile's otherwise
  --token <str>  an `a2a` token; the profile's token otherwise, which the door
                 refuses when it is `*`  [env var: CHOREGOS_A2A_TOKEN]
  --json         print the task as the door answers it, for a script
  --help         Show this message and exit.
```

### `choregos a2a get`

```text
Usage: choregos a2a get [OPTIONS] {task}

  Read a task once: its state, the decision it waits for, its artefacts.

Arguments:
  task  the task id `send` printed  [required]

Options:
  --out <path>   write the artefacts of a completed task in this directory
  --org <str>    the organisation; the profile's otherwise
  --token <str>  an `a2a` token; the profile's token otherwise, which the door
                 refuses when it is `*`  [env var: CHOREGOS_A2A_TOKEN]
  --json         print the task as the door answers it, for a script
  --help         Show this message and exit.
```

### `choregos a2a send`

```text
Usage: choregos a2a send [OPTIONS] {skill}

  Start a workflow: a text, fields and files in one message. The message id is
  printed first.

Arguments:
  skill  the skill, `project/skill` as `choregos a2a skills` lists it
         [required]

Options:
  --text <str>          the request in words, the work item's body
                        (repeatable)
  --field <str>         `key=value` (repeatable), a value read as JSON when it
                        is JSON
  --fields-json <path>  a file holding the fields as one JSON object (`-` for
                        stdin)
  --file <str>          `PATH` or `PATH;type=MEDIA/TYPE`, sent in base64
                        (repeatable)
  --message-id <str>    the idempotency key; by default a digest of the
                        message, so the same command finds the same task
  --title <str>         the work item's title
  --wait                follow the task to its end: exit 0 completed, 3
                        failed, 4 canceled
  --out <path>          write the artefacts of a completed task in this
                        directory
  --org <str>           the organisation; the profile's otherwise
  --token <str>         an `a2a` token; the profile's token otherwise, which
                        the door refuses when it is `*`  [env var:
                        CHOREGOS_A2A_TOKEN]
  --json                print the task as the door answers it, for a script
  --help                Show this message and exit.
```

### `choregos a2a skills`

```text
Usage: choregos a2a skills [OPTIONS]

  The workflows this token may start, and the fields each takes (`*` =
  required).

Options:
  --org <str>    the organisation; the profile's otherwise
  --token <str>  an `a2a` token; the profile's token otherwise, which the door
                 refuses when it is `*`  [env var: CHOREGOS_A2A_TOKEN]
  --json         print the task as the door answers it, for a script
  --help         Show this message and exit.
```

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

### `choregos ask`

```text
Usage: choregos ask [OPTIONS] [message]

  Ask an assistant, as one turn of a conversation the platform keeps.

  The answer streams to standard output; the conversation's id, its cost and
  the writes the assistant proposed (with where to confirm them) go to
  standard error. Exit code 0 when the turn completed, 1 otherwise.

Arguments:
  message  what to say; `-` or nothing reads it from standard input

Options:
  -a, --assistant <str>     the assistant's slug; the only one ready for you
                            otherwise
  -c, --conversation <str>  continue this conversation (its id)
  --continue                continue the assistant's most recent conversation
  --json                    print one JSON object once the turn ended, nothing
                            while it runs
  --org <str>               the organisation; the profile's otherwise
  --help                    Show this message and exit.
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
  it from the console, under My account (the menu under your name,
  `/account`), then pass it with `--token`.

Options:
  --api-url <str>  [default: http://localhost:8000]
  --token <str>
  --org <str>      Default organisation slug (else --org per command or
                   CHOREGOS_ORG)
  --help           Show this message and exit.
```

## `choregos machines`

Applications that call the A2A door under their own name

### `choregos machines create`

```text
Usage: choregos machines create [OPTIONS] {project} {slug}

  Create an account an application calls the A2A door under. It never takes a
  decision.

Arguments:
  project  [required]
  slug     [required]

Options:
  --display-name <str>    what people read; the slug otherwise
  --writes-per-day <int>  the application's own daily budget, 500 by default
  --help                  Show this message and exit.
```

### `choregos machines list`

```text
Usage: choregos machines list [OPTIONS] {project}

  A project's applications and their tokens, never a token's value.

Arguments:
  project  [required]

Options:
  --help  Show this message and exit.
```

### `choregos machines remove`

```text
Usage: choregos machines remove [OPTIONS] {project} {slug}

  End the application: its tokens stop working at once, and its name is not
  reused.

Arguments:
  project  [required]
  slug     [required]

Options:
  --help  Show this message and exit.
```

### `choregos machines revoke`

```text
Usage: choregos machines revoke [OPTIONS] {project} {slug} {token_id}

  Revoke one of the application's tokens.

Arguments:
  project   [required]
  slug      [required]
  token_id  [required]

Options:
  --help  Show this message and exit.
```

### `choregos machines token`

```text
Usage: choregos machines token [OPTIONS] {project} {slug}

  Issue an `a2a` token for the application. The secret is shown HERE, once.

Arguments:
  project  [required]
  slug     [required]

Options:
  --name <str>             [required]
  --expires-in-days <int>  365 at most  [default: 90]
  --help                   Show this message and exit.
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

## `choregos outposts`

Outposts: places that only dial out to the platform, and their instances

### `choregos outposts approve`

```text
Usage: choregos outposts approve [OPTIONS] {slug} {instance}

  Approve a pending instance against the fingerprint its daemon printed.

  The API takes an approval from a person's session that carries a recent
  sign-in, and refuses an API token (403). The CLI signs in with a token: this
  command is therefore refused, and prints the API's refusal as it is. Approve
  the instance in the console, under Administration → Outposts.

Arguments:
  slug      [required]
  instance  the instance's id, as `choregos outposts instances` shows it
            [required]

Options:
  --fingerprint <str>  the fingerprint `choregos-outpost enrol` printed on the
                       outpost's machine, as read there  [required]
  --org <str>          the organisation; the profile's otherwise
  --help               Show this message and exit.
```

### `choregos outposts calls`

```text
Usage: choregos outposts calls [OPTIONS] {slug}

  The recent calls routed to the outpost, newest first: their state and the
  code of a refusal.

  Digests only: the platform keeps no arguments and no result of a call.

Arguments:
  slug  [required]

Options:
  --state <str>   pending, claimed, answered, refused, expired, timed_out or
                  in_doubt
  --limit <int>   how many calls to read, 200 at most  [default: 50]
  --cursor <str>  where the previous page stopped
  --org <str>     the organisation; the profile's otherwise
  --help          Show this message and exit.
```

### `choregos outposts create`

```text
Usage: choregos outposts create [OPTIONS] {slug}

  Create an outpost of the organisation, with no instance yet.

  An instance comes from an enrolment token (`choregos outposts token`) and a
  person's approval. Refused while the deployment keeps outposts switched off.

Arguments:
  slug  [required]

Options:
  --display-name <str>  what people read; the slug otherwise
  --org <str>           the organisation; the profile's otherwise
  --help                Show this message and exit.
```

### `choregos outposts delete`

```text
Usage: choregos outposts delete [OPTIONS] {slug}

  Delete a revoked outpost, with its instances and what is left of its calls.

  Refused while connectors still route to it (`choregos outposts show` names
  them), and while it is not revoked.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts instances`

```text
Usage: choregos outposts instances [OPTIONS] {slug}

  The outpost's instances, pending ones first, each with the fingerprint of
  its signing key.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts list`

```text
Usage: choregos outposts list [OPTIONS]

  The organisation's outposts: status, instances and presence.

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts pause`

```text
Usage: choregos outposts pause [OPTIONS] {slug}

  Pause the outpost: nothing more is handed to it, and what its instances hold
  may finish.

  This is how a healthy outpost is held or retired; `revoke` answers a key
  that may be stolen.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts resume`

```text
Usage: choregos outposts resume [OPTIONS] {slug}

  Resume a paused outpost.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts revoke`

```text
Usage: choregos outposts revoke [OPTIONS] {slug}

  Revoke the outpost and every instance of it. This is final.

  Nothing its instances had in flight is taken, and a write among it ends in
  doubt. Connectors that route to it are refused until they are routed
  elsewhere.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts revoke-instance`

```text
Usage: choregos outposts revoke-instance [OPTIONS] {slug} {instance}

  Revoke one instance of the outpost, pending or approved. This is final.

  The outpost's epoch moves: nothing in flight at the outpost is taken, from
  this instance or another. Its key does not come back for as long as the
  outpost exists.

Arguments:
  slug      [required]
  instance  the instance's id, as `choregos outposts instances` shows it
            [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts show`

```text
Usage: choregos outposts show [OPTIONS] {slug}

  One outpost, and the door's keys to pin in its policy file.

Arguments:
  slug  [required]

Options:
  --org <str>  the organisation; the profile's otherwise
  --help       Show this message and exit.
```

### `choregos outposts token`

```text
Usage: choregos outposts token [OPTIONS] {slug}

  Issue an enrolment token for one instance of the outpost. The token is shown
  HERE, once.

  Single use, valid 15 minutes at most. It is printed with what the outpost's
  operator needs: the lines of the policy file (the door's origin and the
  door's keys to pin) and the `choregos-outpost enrol` command to run there.
  The instance it enrols is born pending, and is handed nothing until a person
  approves it in the console.

Arguments:
  slug  [required]

Options:
  --expires-in-minutes <int>  how long the token lives: 1 to 15 minutes, 15 by
                              default
  --org <str>                 the organisation; the profile's otherwise
  --help                      Show this message and exit.
```

## `choregos plugin`

Plugins: validate, pack and index them (ADR 0093)

### `choregos plugin index`

```text
Usage: choregos plugin index [OPTIONS] {directory}

  Write a source index (plugin-index.schema.json) for every plugin under
  DIRECTORY, located either in an OCI repository or in a git commit.

Arguments:
  directory  A directory whose subdirectories are plugins.  [required]

Options:
  -o, --output <path>     The index to write (index.yaml).  [required]
  --oci-repository <str>  Plugins are published as
                          <repository>/<name>:<version>.
  --git-url <str>         Plugins are read from this repository.
  --git-commit <str>      The commit (40 hex characters) they are read at.
  --git-path <str>        Where DIRECTORY sits in the repository, e.g.
                          plugins.
  --help                  Show this message and exit.
```

### `choregos plugin pack`

```text
Usage: choregos plugin pack [OPTIONS] {directory}

  Validate, then write a reproducible archive of the declared files only
  (plugin.yaml, README.md and LICENSE when present, and what `contents`
  names): sorted entries, time 0, owner 0. The same files always give the same
  bytes, and the printed digest is the one an index records.

Arguments:
  directory  The plugin directory.  [required]

Options:
  -o, --output <path>  The archive to write (.tar.gz).  [required]
  --help               Show this message and exit.
```

### `choregos plugin validate`

```text
Usage: choregos plugin validate [OPTIONS] {path}

  Check a plugin and everything it ships; print its name, version and content
  digest. Only what plugin.yaml declares is read: a server's source or a .venv
  beside it is ignored.

Arguments:
  path  A plugin directory or a packed .tar.gz.  [required]

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

## `choregos self`

The CLI itself: keep it in step with the instance

### `choregos self update`

```text
Usage: choregos self update [OPTIONS]

  Replace this CLI with the instance's own build, for this OS and processor.

  The binary comes from the instance (`/downloads/cli/`), in the version it
  runs, and is checked against the sha256 of its manifest before it replaces
  the running one in a single rename. A CLI installed from Python packages has
  no binary to replace: upgrade the package, or install the binary with the
  instance's install script.

Options:
  --api-url <str>  The instance to update from (default: the profile's)
  --path <path>    The binary to replace (default: the running one)
  --force          Download even when the versions are the same
  --help           Show this message and exit.
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
