# 0201 — A person hands files to a work item, and a step reads them as `attachments`

- **Status**: proposed, 2026-11-20 (written by story S85-01 from the product owner's request of the
  same day; accepted when the owner merges it)
- **Concerns**: the workflow DSL (`workflow.schema.json`: a new `metadata.attachments`, a reserved
  input name), the REST contract (a new `POST /work-items/{id}/files`), `services/artifacts.py`, the
  run's inputs (`GET /internal/runs/{id}/inputs`), the validator and the artifact flow
  (`tranibor_core.dsl`), the console's new work item form and work item page; extends
  [ADR 0049](0049-an-artifact-is-typed-stored-and-traced.md) (artifacts) and
  [ADR 0078](0078-governed-workflows-as-a2a-skills.md) §6 (files sent through the A2A door); leaves
  [ADR 0031](0031-several-workflows-per-project.md) as it is (`metadata.inputs` stays fields);
  stories S85-03 to S85-06

## Context

Some work starts from documents a person received: quotes to compare, a file of certificates to check,
a contract to review, a statement to reconcile. The platform already knows what such a document is
once it is inside: an artifact (ADR 0049), typed, stored, hashed, its digest checked on every read, and
a step that declares its name in `inputs` finds it in `.choregos/inputs/`. The vocabulary of types
already holds `pdf`, `docx` and `xlsx`.

What is missing is the way in, and the way to a step:

- **Only an application can hand a file.** The A2A door turns a file part into an input artifact
  (ADR 0078 §6, amended 2026-10-23). A person cannot: `WorkItemCreate` carries a title, a body, fields
  and labels; the console's form draws text, date, number, boolean, choice and list controls; the MCP
  tool `create_work_item` and the HTTP trigger take no file.
- **A step must know a file's name before it exists.** `inputs` lists exact artifact names, and a
  handed file is named after its file name. A workflow written before the files arrive cannot list
  them, so even the A2A door's files reach only a step that guessed their names.

Both are gaps of the platform, not of a trade: nothing below names one.

## Decision

1. **A workflow says that it takes files.** `metadata.attachments: {accept, min, max}`. `accept` is a
   list of artifact types (names of the contract's vocabulary: `pdf`, `docx`, `xlsx`, `csv`,
   `markdown`, `text` …); `min` defaults to 0; `max` defaults to 20 and is at most 50. A workflow
   without the block takes no file from a person. `metadata.inputs` is untouched: fields are JSON
   values, validated by a schema and shown as values.
2. **One route hands them**: `POST /work-items/{id}/files`, multipart, one or several files, for
   whoever holds `item:control` on the work item's project. Each file becomes an **input artifact** of
   the work item, stored exactly as an A2A file is (the object store first, then the row; a store that
   fails leaves no row). Its producer is `person:<user id>`, beside `a2a:<task id>`: a revision keeps
   saying who or what it came from.
3. **A file is checked before it is stored.** Its type must be in `accept`; its bytes must be what
   its type says (the magic-byte and archive checks a knowledge-base upload already passes); its size
   must be within the project's limit for handed files, which is the limit of ADR 0078 §6
   (`a2a.max_file_bytes`, 10 MiB by default, 50 MiB at most): one limit for a file handed to a work
   item, whatever the door. The setting keeps its name: a project's configuration is part of the REST
   contract that ADR 0168 freezes, and a rename is not worth a window. This record is where its wider
   meaning is written. The count of distinct handed names stays within `max`.
4. **A file's name is a slug of its file name.** ASCII folding, every character outside
   `[A-Za-z0-9_.-]` replaced by `-`, runs of `-` collapsed, the extension kept, 128 characters at most
   (the artifact name pattern); an empty result is refused. Two files of one request that reach the
   same name are refused together: the platform does not choose which one a person meant.
5. **Revisions follow ADR 0049 §6.** The same name again is a new revision; the same bytes again make
   none.
6. **A file may be handed in any state that is not terminal.** A person answering a step that was
   sent back often needs to add one. A terminal work item refuses (409).
7. **A start waits for the files a workflow requires.** With `min` ≥ 1, creating a work item with
   `start: true` is refused (422, naming the path), and `mark_agent_ready` is refused (409) while
   fewer than `min` handed files exist. A work item is filed with `start: false`, given its files,
   then started. Files that came through the A2A door count toward `min`.
8. **`attachments` is a reserved input name.** A transition that lists it in `inputs` receives every
   handed file of its work item (producers `person:` and `a2a:`), the latest revision of each name,
   mounted in `.choregos/inputs/` like any declared input, with the same digest check and the same
   read receipt. The runner does not change. A step that does not declare `attachments` is served
   none of them, as today.
9. **The validator knows the name.** `attachments` as an output is refused, like `diff`
   (ADR 0129 §7). `attachments` as an input of a workflow that declares neither `metadata.attachments`
   nor an `a2a:` block is refused when the workflow is written: it could never receive a file. In the
   artifact flow its origin is `attached`, never `missing`.
10. **Each file leaves one audit entry**: who, which work item, the name, the digest, the size; never
    the content.
11. **The console follows.** The new work item form shows a file control when the chosen workflow
    declares `metadata.attachments`, states the accepted types and the bounds, and cannot be sent
    under `min`. The work item page lists the handed files, who handed each and when, and offers to
    add some while the item is not terminal.

## What a handed file is not

- **Not a prompt.** The platform never places a handed file in a prompt. It reaches a step as a file,
  and the runner's task description already introduces declared inputs as data, not instructions.
- **Not converted by the API.** The API stores bytes. Turning a document into text is a step's work,
  in the step's sandbox, where a hostile file costs one run.
- **Not knowledge.** A knowledge base is a corpus a person curates and many runs read. An attachment
  is the evidence of one work item, read by the steps of that work item that declare it.

## Consequences

- A workflow from any trade can start from a person's documents, and its first step is free to be a
  deterministic one that measures what could and could not be read.
- An application that sends files through the A2A door no longer needs the workflow to know their
  names: a step that declares `attachments` reads them.
- The contract grows by one block, one route and one reserved name. Each is frozen with the surfaces
  of 1.0 (ADR 0168), so the route's shape is decided here once.
- A file's producer is no longer always a run or an A2A task: the readers of `run_id` that tell a
  handed file from a step's output (`sent_by_caller`) read both producers.

## Not decided here

- **Files through the MCP door and the HTTP trigger.** A tool call or a JSON payload would carry a
  file as base64 text; both wait for a use that needs it.
- **Named slots**: telling, among the handed files, which is the request and which are the answers,
  or which belong together. A step, or a person's review, makes that assignment today.
- **A knowledge base per work item.**
- **Reading scanned documents** (OCR). A step that cannot read a file says so in a measured fact.

## Rejected alternatives

- **A `file` type inside `metadata.inputs`.** Fields are JSON: validated by a schema, drawn as values,
  compared by gates, editable by a person. A file is none of that, and a field holding a reference to
  one would be a second, weaker artifact.
- **A pattern in `inputs:`** (`*.pdf`). The artifact flow says where each document of a workflow
  comes from; a pattern would make that a guess, and would let a step read another step's output it
  never named.
- **Hand the files at creation, in one multipart request.** It would make the creation route two
  routes in one, with a JSON part to validate inside a form; and a person adding a file to a work
  item that was sent back needs the second route anyway.
- **Let every step read every handed file.** A step reads what its transition declares (ADR 0049);
  an agent that needs no document should not be given a stranger's.
