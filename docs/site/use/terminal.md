---
title: The terminal
description: Sign in as yourself from a terminal, ask an assistant, follow what runs, decide what waits for you, fetch artefacts, and read the colours.
sidebar_position: 9
---

# The terminal

The `choregos` command is a surface of the platform, like the console: the same assistants, the same
activity, the same decisions, under your rights and your organisation's audit. It runs nothing on your
machine: no model, no agent, no tool, no file edit. Everything it shows, and everything it sends, goes
through your instance.

**What you will be able to do**

- Install the command from your instance and sign in as yourself.
- Ask an assistant a question, from a prompt or from a script.
- Follow what runs: the tree of projects, work items and runs, then one run's steps.
- Work in one screen: conversation, live tree and decisions side by side.
- Approve or refuse what waits for you, one decision at a time.
- Read the colours, and turn them off.

## Before you start

- The address of your instance (here `https://choregos.example.com`) and an account on it.
- To ask, an assistant installed for a group you belong to (see [Assistants](assistants.md)).
- To decide, the same role and group the decision needs in the console (see [Decisions](decisions.md)).

## Install

Your instance serves the command built for its own version, as a single file for Linux, macOS and
Windows. On Linux and macOS:

```bash
curl -fsSL https://choregos.example.com/cli/install.sh | sh
```

The [CLI install page](../cli/install.md#from-your-instance) has the Windows script, the files to download
by hand and how to check them.

## Sign in

```bash
choregos login --api-url https://choregos.example.com --org acme
choregos whoami
```

`choregos login` opens your browser on the console, which shows a short code. Check that it is the one
the terminal printed, then approve. You sign in as yourself: the CLI holds a **session** of yours, never
a copy of a token you could reuse elsewhere.

- **Over SSH, or on a machine without a display**, nothing opens: the terminal prints the link, and you
  open it in a browser anywhere. `--no-browser` does the same on purpose.
- **Waiting** ends when you approve, refuse in the console, or press Ctrl-C (nothing is saved then). A code
  that was not approved in time expires: run `choregos login` again.
- **`--fresh`** makes the console sign you in again at your identity provider before it asks you to approve.
  Use it when a decision says your sign-in is too old ([below](#decide)).
- **`--token`** saves an access token instead of opening a browser. It is for scripts and CI. A token is
  not a session of yours: it can read and write what its role allows, but it cannot decide (see
  [Decide](#decide)).
- **Sessions**: each sign-in is listed under **My account**, *Sessions*. Ending it there signs the CLI out.
  `choregos logout` ends it from the terminal and removes the credential from the profile.

```bash
choregos login --api-url https://choregos.example.com --token "$CHOREGOS_TOKEN" --org acme
choregos login --fresh
choregos logout
```

The profile is saved in `~/.config/choregos/config.json`. `ask`, `activity` and `chat` take `--org` to name another
organisation than the profile's.

## Ask

```bash
choregos ask "What did we invoice Globex in October?"
choregos ask --assistant billing "And in November?" --continue
```

`ask` is one turn of a conversation the platform keeps. The answer streams to standard output, and **only
the answer does**: the conversation's id, the tokens and the money spent, and any write the assistant
proposed go to standard error. So `choregos ask "…" > answer.md` keeps the answer alone.

- **Which assistant.** `--assistant SLUG`, or the only one that is ready for you in the organisation. When
  several are, the command names them and stops.
- **From standard input.** Without a message, or with `-`, the message is read from standard input. An
  empty message is refused before anything is sent.
- **Continue.** `--continue` picks the assistant's most recent open conversation; `--conversation ID` picks
  one by its id. The conversation is the same one the console shows, so you can carry on there.
- **A proposed write.** An assistant never writes on its own: when it proposes a write, the terminal prints
  what it wants and a link to confirm or refuse it. Confirm there, or with [`choregos decide`](#decide).
- **For scripts.** `--json` prints nothing while the turn runs, then one JSON object: `conversation_id`,
  `status`, `text`, `pending_actions`, `usage` and `error`.
- **Exit codes.** 0 when the turn completed. 1 when the turn failed, or was not sent because no
  assistant could be chosen: the reason is printed. 130 after Ctrl-C, which cancels the turn on the platform; what
  it already spent is booked.

```bash
echo "Summarise yesterday's failed runs" | choregos ask -
choregos ask --json "How many invoices are overdue?" > turn.json
```

## Follow what runs

```bash
choregos activity
choregos activity --watch --project billing-api
```

`activity` draws what runs in the organisation as a tree: project, work item, run (stage, agent, model),
then your own sessions. Each line has a status (a glyph and a word), the elapsed time, the tokens and the
cost so far. A cost the platform cannot say shows as "—", never as 0. Below the tree, what ended in the
last hour; last, a line `N running · $X today`. It only reads: stopping stays in the console and in the
[workspace](#the-workspace).

- **`--watch`** keeps the tree live until you press Ctrl-C or the stream ends. Elapsed times move every
  second.
- **Filters.** `--project SLUG`, `--workflow SLUG` (the work items born in that workflow), `--agent SLUG`,
  `--kind run|session|turn`.
- **Sub-agents.** When a run launched sub-agents, they are drawn under their run, with their own status
  and tool-call count.

For one run, give its id:

```bash
choregos activity --run RUN_ID
choregos activity --run RUN_ID --watch
```

It shows the plan the agent declared, its tool calls (state, time, title) and what fills the model's
context now. A backend the platform cannot see into shows "Context: not measured". `--run` takes none of
the tree's filters. With `--watch`, it follows the run until it ends.

## The workspace

```bash
choregos chat
choregos chat --assistant billing
choregos
```

`choregos chat` opens the workspace: the conversation and its prompt on the left, the live tree (blue
frame) and what waits for you (amber frame) on the right, what moved elsewhere below, and a status line.
A bare `choregos` opens the same thing, but only when both its input and output are a terminal: anywhere
else (a pipe, a script) it prints the help, so no script that calls it changes behaviour. `choregos chat`
itself refuses to run without a terminal and points to `ask` and `activity`.

| Key | Does |
| --- | --- |
| Tab | Moves to the next pane |
| Enter, in the tree | Expands a project, a work item or a run (plan, tool calls, context, sub-agents) |
| `x`, in the tree | Stops the run or work item under the cursor, **after asking why** |
| Esc | Cancels the turn being answered; the platform books what it spent |
| Ctrl-O, Ctrl-N, Ctrl-A | Switches conversation, starts a new one, switches assistant |
| F2, or `d` in the decisions pane | Opens a decision (see [Decide](#decide)) |
| Ctrl-Q | Quits |

A stop always needs a reason: an empty one is not sent. The reason is audited and shown on the work item.
`x` says so when you may not stop what is under the cursor. The footer lists the keys that apply to the
pane you are in.

## Decide

```bash
choregos decide list
choregos decide show ID
choregos decide approve ID
choregos decide reject ID --reason "wrong cost centre"
choregos decide answer ID --answer "Ship the patch release"
```

`decide list` shows what waits for you across your organisations, newest first: a write or a workflow
start an assistant proposed, a governed action awaiting an approver, and a human step of a workflow.
`show` displays one without deciding anything.

The rules are the same here as in the workspace:

- **You see it first.** The operation, its arguments and the source's diff, when there is one, are shown
  before you decide.
- **One gesture decides one thing.** The id is one argument and the verb is typed: `approve`, `reject` or
  `answer`. There is no default, no `--yes`, no `--all`. After the details, the command asks you to type
  the verb; anything else leaves the item waiting and sends nothing.
- **A reason to refuse.** Every rejection asks for a reason (give it with `--reason`, or you are asked): a
  governed action, a step, and an assistant's proposed write or launch alike. An empty one sends nothing. The
  audit keeps it; for an assistant's write it is also kept on the action and the assistant is told it.
- **Not everything is decided here.** A task's form and a scope change stay in the console, and the
  command says so.

**Why a pasted token is refused.** A decision has to be yours, so it needs a session of yours: a token
saved with `--token`, or one pasted into `CHOREGOS_TOKEN`, may read but cannot decide. The platform
answers `decision_requires_session`, and the command tells you to run `choregos login` and repeat it.

**When your sign-in is too old.** Some decisions need a recent sign-in. The command then says so: run

```bash
choregos login --fresh
```

and repeat the decision.

## Artefacts

```bash
choregos artifacts list WORK_ITEM
choregos artifacts inbox --limit 20
choregos artifacts download WORK_ITEM report.md ./out
choregos artifacts open WORK_ITEM report.md
choregos artifacts compare WORK_ITEM report.md@1 report.md@2
choregos artifacts diff RUN_ID
```

`list` shows every revision of every artefact of a work item; `inbox` the artefacts your tasks delivered,
newest first. A revision is `name` (the latest) or `name@version`. `download` saves the bytes exactly as
the platform served them to a path, a directory or the current one, and keeps an existing file unless you
pass `--force`. `open` uses your system's viewer from a temporary copy. `compare` prints a unified diff of
two revisions of a text artefact, and `diff` what a run changed, file by file. `list`, `inbox` and `diff`
take `--json`.

## Colours

Colour orients, and is never alone: every status is a **glyph, then a word, then a colour**, so a
monochrome terminal, a pipe or a screenshot in black and white loses nothing.

| Status | Glyph | ASCII | Word | Colour |
| --- | --- | --- | --- | --- |
| Running | ● | `*` | running | blue |
| Queued | ○ | `o` | queued | grey |
| Waiting for a person | ⏸ | `!` | waiting for you | yellow |
| Done | ✓ | `+` | done | green |
| Failed | ✗ | `x` | failed | red |
| Stopped | ■ | `#` | stopped | grey |

Blue frames the live tree and amber frames decisions; red is only ever a failure. The colours are your
terminal's own sixteen, so your theme sets the contrast, and no background is painted.

- **Turn colour off** with the `NO_COLOR` environment variable (any non-empty value), with `TERM=dumb`, or
  with `--no-color` before the command. Output then has no escape sequence at all, and the glyphs and words
  stay.
- **ASCII fallback.** When the terminal's encoding is not UTF-8, the glyphs and the bars are drawn with
  the ASCII characters of the table.
- **Force colour** in a place that is not a terminal (a CI log that renders it) with `FORCE_COLOR=1`.
  `NO_COLOR` still wins.

```bash
choregos --no-color activity
NO_COLOR=1 choregos activity --run RUN_ID
TERM=dumb choregos activity
FORCE_COLOR=1 choregos activity
```

## What can go wrong

| Symptom | Why | Fix |
| --- | --- | --- |
| `several assistants are ready for you` | The organisation has more than one assistant for you | Pass `--assistant` with one of the slugs it lists |
| `no assistant is ready for you` | None is installed for your groups, or its owners have not completed it | Ask an organisation administrator |
| A decision says `decision_requires_session` | You signed in with `--token`, or pasted a token | Run `choregos login`, then repeat the decision |
| A decision says your sign-in is too old | The decision needs a recent sign-in | Run `choregos login --fresh`, then repeat it |
| `choregos chat` says it needs a terminal | Its input or output is a pipe | Use `choregos ask` or `choregos activity` in scripts |
| `no organisation` | Neither `--org`, the profile nor `CHOREGOS_ORG` names one | Pass `--org SLUG`, or sign in with `--org` |
