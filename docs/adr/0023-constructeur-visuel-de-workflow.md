# 0023 — A visual workflow builder: read first, then edits that are YAML diffs

- **Status**: proposed, 2026-09-25 — phase 1 delivered (read-only map), phase 2 not started
- **Concerns**: `/p/<slug>/workflow`, `choregos_core.dsl.graph.to_graph`, `PUT /projects/{id}/workflow`

## Context

A workflow is a YAML file validated by `choregos_core.dsl` — the same code in the API, the
CLI and the orchestrator (ADR 0001, 0010). The web editor shows the YAML on the left and,
since P0-5, a **map** on the right computed by the validator (`report.graph`): one lane per
kind of actor, one column per nominal step, secondary edges dashed, a deterministic layout.
Since the keyboard work (P2) the map is navigable and describes each state with its
transitions, actors and gates.

Every competitor with a visual builder (n8n, Microsoft Agent Framework's designer, Dify)
stores the graph as its own model and generates code or YAML from it. That is the wrong way
round for us: the YAML is what the validator checks, what Git reviews, what the replay
histories reference. A second model would need a second validator.

## Decision

1. **Phase 1 is done and stays**: the map is a *view* of the validated YAML, never a
   document of its own. It has no state the YAML does not have.
2. **Phase 2 edits are operations on the YAML**, not on the graph. The map offers a small
   set of intents — add a state in a lane, add a transition between two states, attach a
   gate to a transition, rename a display — each sent to the API as an operation
   (`POST /workflows/edit` with `{yaml, op}`) that the server applies with a
   round-tripping YAML library (comments, key order and quoting preserved), validates, and
   returns as **new YAML plus the validation report**. The editor shows the resulting diff.
   Saving is still `PUT /workflow` with the YAML.
3. **What the graph refuses to do**: free drag-to-connect, node positions saved anywhere,
   any edit the operation set cannot name. If the intent cannot be written as a diff a
   reviewer would accept, it is not offered.
4. **Fidelity is tested before any intent ships**: for every demo and template workflow,
   applying an operation then its inverse yields the original bytes.

## Conditions

- The operation set is small on purpose and grows one intent at a time, each with its
  round-trip test and its validator case.
- No intent bypasses a validator refusal: an added transition whose gate would have nothing
  to check is refused exactly as it is in the YAML editor.

## Consequences

- People who do not read YAML can add a human approval before production, and what lands in
  Git is a three-line diff their reviewer can read.
- One model, one validator, one review path; the builder cannot drift from the DSL.

## Alternatives discarded

- **A graph model persisted alongside the YAML** (positions, groups): two sources of truth,
  a migration every time the DSL moves, and layouts that lie after a hand edit.
- **Full freeform editing with YAML generation**: the generated YAML is not reviewable, and
  the validator becomes the only reader of it.
