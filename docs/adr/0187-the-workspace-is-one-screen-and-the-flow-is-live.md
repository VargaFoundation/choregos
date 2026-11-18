# 0187 — The workspace is one screen of tabs, and a workflow's flow is shown live

- **Status**: accepted, 2026-11-18 (the product owner's request and go of the same day)
- **Concerns**: `packages/cli` (the `choregos chat` workspace, a new `choregos live`), `apps/web` (a new
  `/p/{slug}/live` page), `ActivityEntry` in `packages/contracts/openapi.yaml` (one optional field),
  `services/activity.py`; amends [ADR 0154](0154-the-terminal-is-a-surface-of-the-platform.md) §3 (the
  workspace's screen model) and [ADR 0180](0180-one-quarter-of-depth-the-scope-is-frozen.md) §2 (the row of
  S67); builds on [ADR 0031](0031-several-workflows-per-project.md) (the board's columns are the workflow's
  states), [ADR 0120](0120-colour-orients.md), [ADR 0141](0141-tokens-and-the-context-are-read-per-scope.md),
  [ADR 0142](0142-a-stop-interrupts-what-is-in-flight.md); stream S67

## Context

The product owner, on 2026-11-18, after a day in `choregos chat` on the production instance:

1. *"The rendering is a bit heavy between conversations, 'say something'."* The workspace of ADR 0154 §3
   shows five framed panes at once (conversation, live tree, decisions, two "elsewhere" panes), the
   composer is a sixth frame, the turn's cost sits in a border.
2. *"Some steps are in error, why?"* A failed run reads `✗ failed` and nothing else; the reason
   (`loop`, `budget`, `max_turns`, a gateway refusal) is in the run's `StageResult`, which the activity
   list does not carry. A human step opened after a failure shows the gateway's raw JSON as its title.
3. *"How can I see all the projects?"* The tree shows only the projects with a run in the last hour;
   `choregos projects list` exists but the workspace has no way to it.

And: *"Make it more complete and at the same time cleaner"*, and *"a live mode, for the CLI and the web,
where I can see the workflow processing the items — something stylish."*

ADR 0180 froze the scope on 2026-11-17 and let S67 finish with "nothing new opened". The product owner,
who decided ADR 0180, asks for this work; it is recorded here as an amendment of ADR 0180's row for S67
rather than done silently.

## Decision

### 1. The workspace is one screen of tabs

`choregos chat` (and a bare `choregos` in a terminal) shows **one main view at a time** under a one-line
header, chosen by tab or key:

| Key | Tab | What it shows |
|:--|:--|:--|
| `1` | Conversation | the turn's transcript, full height; the composer is one line `›` at the bottom |
| `2` | Activity | the tree of ADR 0154 §2, failures with their reason (§3) |
| `3` | Projects | every project of the organisation the person may read, then a project's items, then an item's runs |
| `4` | Decisions | what waits for the person, one line each, grouped by project |
| `5` | Artefacts | the inbox and a work item's artefacts (S67-11) |
| `6` | Live | the live flow of §4 |

A **context pane** on the right follows the selection: the run a conversation started, the item under the
cursor, the detail of a decision. It folds below 120 columns.

**No frames.** Panes are separated by one rule, titles are words in the header, not borders. The header
carries the organisation, the assistant, `N running · N waiting for you · amount today`; the composer
carries the turn's cost. Colour keeps the meanings of ADR 0154 §3 (blue what runs, amber what waits,
red what failed); `NO_COLOR` and ASCII still carry every status by glyph and word.

**A palette** (`ctrl-k`) reaches everything by name: a tab, a project, an item, an assistant, an
organisation, and the CLI's own commands that make sense inside the workspace (decide, stop, relaunch).
Every key stays in the footer; nothing is reachable only through the palette.

**One currency**: every amount the CLI prints is in the organisation's currency setting (`EUR` or `USD`,
S29-03), with its symbol; an amount the API gives only in the other currency is converted with the rate
the API states, or printed with its own symbol, never relabelled.

### 2. Projects are a tab, not a side effect of activity

The Projects tab lists every readable project (`GET /orgs/{org}/projects`, the same rows as `choregos projects
list`): name, items in flight, items waiting for a person, spend this month. `Enter` opens its items
(`GET /projects/{id}/work-items`), `Enter` again an item's runs and steps. The Activity tab stays what runs
now; the Projects tab is what exists.

### 3. A failure says why, in one line

`ActivityEntry` gains an optional `failure: {reason, summary}` for an entry whose status is `failed`:
`reason` is the run's `StageResult.reason` (an open string: `loop`, `budget`, `max_turns`, `timeout`,
`gateway`, …), `summary` its `summary` cut to 300 characters, with credentials redacted as the journal is.
A surface prints the reason as a word (`loop`, `budget spent`, `too many tool calls`) and the summary on
demand. A pending item whose title is longer than one line is shown on one line; its full text is in the
context pane. No surface prints a provider's raw error body in a list.

### 4. A workflow's flow is shown live, on the web and in the terminal

A **live view** of one workflow (or of a project's workflows, one band each): the workflow's states as
**lanes** (the board's columns, ADR 0031, read from the validated graph), each work item a **card** in the
lane of its state. A card whose item has a run in flight shows the stage, the agent, the elapsed time
and the spend ticking, and a pulse; a card moves to its next lane when its state changes; a failed run
turns the card red with §3's reason; a card waiting for a person is amber with what it waits for. Each lane
header counts the items in it and the items that left it today. A ticker at the bottom lists the last
events (a run started, ended, failed; an item moved; a decision taken).

Its data are **routes that exist**: the workflow definition and its validation (lanes), `GET
/projects/{id}/work-items` (cards), `GET /orgs/{org}/activity/stream` filtered by project and workflow (what
runs, `activity.upsert` and `activity.ended`); on `activity.ended` the item is read again, which is how a
card learns its new state. No new route, no polling faster than the stream.

**Web**: `/p/{slug}/live`, linked from the board and the project's navigation; a "wall" mode (full
screen, larger type, no navigation) for a screen in a room. Motion uses CSS transitions and the
browser's View Transitions where available, no animation dependency; `prefers-reduced-motion` turns
every motion into a plain change of place.

**Terminal**: `choregos live [--project] [--workflow]` and the workspace's Live tab draw the same lanes as
columns of cards (a lane per column, narrow lanes collapsing to counts below 100 columns), with a spinner
on running cards and the ticker as the last lines.

**One reduction, two renderers**: the step from (definition, items, stream events) to (lanes, cards,
ticker) is a pure function written twice, in TypeScript and in Python, and both are tested against the
same recorded fixtures (`packages/contracts/fixtures/live/`), so the web and the terminal cannot show a
different flow for the same events.

### 5. What this does not do

- No new write: the live view and the tabs read; decisions keep their one-gesture screens of ADR 0154 §7.
- No drag and drop of cards between lanes: a state changes through the workflow, not through the view.
- No new endpoint; if the stream proves too coarse for the live view, a later record adds one.

## Consequences

- The workspace's SVG snapshots (ADR 0154 §3) are replaced; the old five-pane layout is not kept behind a
  flag.
- `ActivityEntry.failure` is a contract change (contract-first pull request, then the service).
- The live view's two reductions must stay equal: a fixture added for one is run by both.
- ADR 0180's row for S67 becomes "finish, plus the workspace redesign and the live view of ADR 0187
  (S67-18 to S67-28)". No other stream is opened.
