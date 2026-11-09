# 0129 — An artifact says how it is shown

- **Status**: accepted, 2026-11-09
- **Concerns**: the artifact contract (`packages/contracts`, `artifact.schema.json`), the workflow DSL
  (`workflow.schema.json`, a new top-level `artifacts:` block), the stage input, the runner's result
  check, the work item page; extends [ADR 0049](0049-an-artifact-is-typed-stored-and-traced.md) and
  builds on [ADR 0045](0045-the-provenance-of-a-signal.md) (measured or declared), ADR 0123 (a work item
  may change workflow) and ADR 0029 (workflows from any trade)

## Context

ADR 0049 typed artifacts by format, and a value nobody declared falls back on its Python type: text is
`markdown`. On the production database of 2026-11-09, 31 artifacts out of 32 are `markdown`, among them
`size` (`S`), `risk` (`low`), `verdict` (`approve`) and `confidence`. The console therefore:

- has one tab per output, in alphabetical order, three of them holding one word;
- shows a ticket's size and risk nowhere on its page, although both are columns of the work item;
- shows no diff: `collect_run_artifacts` reads each file's patch from the SCM, then keeps only the path
  and the counters, so the API's `patch` field is always empty;
- cannot put what matters where it is read, because nothing says what matters.

## Decision

1. **Three questions, three answers.** The *type* says what a value is (how it is checked and drawn).
   The *scale* says what the levels of a `level` are worth (their order, their tone). The *placement*
   says where it is seen. They are declared separately, and none of them is a trade's vocabulary: `risk`
   is not a type, it is a scale of the type `level`.
2. **Four new types**, in the same vocabulary as ADR 0049, all inline:
   - `level`: one word of an ordered scale. Kind `value`.
   - `metric`: one finite number, with an optional unit, bounds and direction (`better: higher|lower`).
     Kind `value`.
   - `keyvalue`: one flat object of scalars. Kind `json`.
   - `diff`: a unified diff. Kind `diff`, media type `text/x-diff`.
3. **Scales are data.** The contract ships `risk`, `size` and `verdict`. A workflow may write its own
   `levels`, each a value and a tone. A tone is a word of a closed list (`neutral`, `info`, `ok`, `warn`,
   `danger`): a workflow never writes a colour.
4. **Declared by name, once per workflow.** A top-level, optional `artifacts:` block, keyed by output
   name, because one name can have several producers (`verdict` comes out of `t-review` and of
   `t-security-review`) and a per-transition declaration could contradict itself. `output_types` stays
   valid and unchanged for formats.
5. **The placement** is `show`: `facts` (the *About this ticket* card), `tab` (the default) or `hidden`.
   `facts` accepts only what fits on one line — `level`, `metric`, `diff` — and at most eight names. A
   report in that card is refused when the workflow is validated.
6. **A ticket's size and risk are always shown**, without any declaration. They are the work item's
   columns, not artifacts: a person or the tracker can set them, the agent's output only proposes them,
   and a work item keeps the workflow version it started with, so a new declaration would never reach
   it. The artifacts named `size` and `risk` remain their history.
7. **The diff is measured, never declared.** `diff` is a reserved name: no output may take it, and a
   value an agent writes under that name is not recorded. The platform records it after each step that
   left the branch different, from the SCM's comparison of the pushed branch (the pod is gone, what the
   agent could have rewritten locally does not count). The workflow only decides how it is shown. It is
   the whole branch against its base, so ADR 0049 §6 makes a new revision only when it changed.
8. **No secret reaches a row that never changes.** The diff is recorded before the gates run, and a row
   is never modified. The patch and its paths are scanned first, wider than the `no_secrets` guarantee
   (fine-grained GitHub and GitLab tokens, any private key, a literal assigned to a secret-named key):
   a file where a secret shape is found keeps only its name and counters, even its name hidden if the
   secret is in the path, and the revision says the patch was withheld and which shapes were found, never
   their values. The patch is also bounded (`CHOREGOS_ARTIFACT_DIFF_MAX_BYTES`, 1 MiB by default), file
   by file, and says when it was cut.
9. **A declaration is snapshotted on the revision** (a nullable `display` column), because a work item
   can change workflow (ADR 0123) and a revision must stay readable under the scale it was produced
   with. The digest covers the content only, so ADR 0049 §5 and §6 are untouched. A revision without a
   snapshot — every row older than this ADR — gets the display of its well-known name at read time; its
   row is not rewritten.
10. **A typed value is checked twice.** The runner sends a value off its scale, out of its bounds or of
    the wrong shape back to the agent, like a missing output, and the prompt names the allowed values.
    The API converts what can be converted (`"0.9"`, `"87%"`, the case of a level) and, for an agent the
    runner does not supervise, records what it cannot convert under its plain type, without a display.
    `outputs_in` remains the guard that routes: a scale says what a value may be, a gate says what this
    step accepts.
11. **The list carries the values that fit on one line** (`level`, `metric`), after the same digest check
    as any read, so the card asks for one list instead of one request per fact. A value that no longer
    matches its digest is absent from the list.
12. **An Office file is previewed, by the console itself** (S61-18). A `docx`, `pptx` or `xlsx` is a ZIP of
    XML, read in the browser into plain data the console draws as its own elements: slides as positioned
    boxes with their theme and the inheritance of placeholders, a document in reading order, sheets of
    values. Nothing of the file runs: no markup or macro is used, an external link is never followed, a
    picture is shown only if its bytes are raster, and inflating stops at a bound whatever the archive
    declares. The page says it is a preview; the download remains the exact file. ADR 0049 §8 stands: the
    platform generates nothing and no Office library enters the core.

## Consequences

- `apps/api/tests/test_typed_artifacts.py` and `apps/orchestrator/tests/test_diff_artifact.py` prove
  decisions 7 to 11; `packages/contracts/tests/test_artifact_display.py` and the validator's tests prove
  decisions 2 to 5.
- `StageInput` gains a field: an old runner image refuses it, so the job images are released before the
  chart, as for every change of that contract.
- No change under `apps/orchestrator/.../workflows/`: the declaration is read by the activity that
  prepares the stage, from the work item's pinned definition.
- A ticket that was already past its last agent step gets no diff: the branch may be gone, and no
  backfill is attempted.
- A confidence an agent declares is shown as declared. A gauge would suggest a calibration nobody
  measured; only the decision ledger (ADR 0121) measures one.

## Alternatives discarded

- **`risk`, `tshirt` and `status` as types.** One core release per trade's vocabulary, against ADR 0029.
- **`status` as an artifact.** An artifact is recorded when its run ends, so `RUNNING` could never be
  true of one, and the state of a ticket is its state machine's. An outcome is a `level`.
- **The diff as a declared output.** It would be the agent's account of its own work, cost tokens, and
  be stale after the next step.
- **The diff measured by the runner** (`workspace.diff_text`). Exact, but inside the pod the agent worked
  in; the pushed branch is what will be merged.
- **Declaring display on each transition.** See decision 4.
- **A colour in the YAML.** The console's tones are tested for contrast in both themes; a free colour is
  not.
- **Drawing Mermaid in the browser.** Tried on 2026-11-09 with `mermaid` 12.2.0, loaded on demand, its SVG
  shown in an `<img>`: it works, and takes the console's JavaScript from about 2 MB to 7.2 MB, the largest
  chunk 1.4 MB, against a budget of 2.2 MB and 600 kB. A guard that exists to catch this is not raised for
  it. A `mermaid` artifact stays text; rendering it on the server is the way to reopen this.
- **Converting Office files to PDF with LibreOffice.** The exact rendering, for an image about 600 MB larger
  and a parser of agent-written files to isolate and keep patched. It is the way to reopen fidelity if a
  preview is not enough.
- **`mammoth`, SheetJS and a PPTX library in the browser.** Megabytes for three formats, against a 2.2 MB
  budget, and no maintained PPTX renderer.
- **A geographic map.** It needs a tile provider, a wider Content-Security-Policy, and sends what the
  map shows to that provider. Not without a use case.
