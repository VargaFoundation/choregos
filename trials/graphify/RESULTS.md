# Graphify on choregos — does a graph make a project easier to understand?

2026-10-27 · graphifyy 0.9.96 · corpus choregos @ 56a39e7e (2 975 files) · budget 10 $, spent **4.16 $**

## Verdict

**No measurable gain.** On 20 project-understanding questions × 3 repetitions, an agent with
the graph answers no better than the same agent with grep/read, whether the graph covers the
code only (B) or the code plus the ADRs and reference docs (B2). The blind judge gives all three
arms the same score (1.75 / 1.72 / 1.72 out of 2); per question, B2 beats A on 3, loses on 5,
ties 12. The graph costs more to build than all 180 agent runs together, and building it from
docs is slow and fragile.

## Protocol

- Questions and their ground truth were frozen before the first run (sha256 `9c70bc7e…`),
  written from the code and ADRs, none shaped for the graph: 8 *locate* (which file),
  6 *why* (which ADR, which reason), 6 *trace* (a chain of components).
- Arms, same model (Haiku), same 30-turn cap, tools Read/Grep/Glob, no Bash:
  - **A**: no graph.
  - **B**: + graphify MCP on the code-only graph (35 052 nodes, local AST, 0 $).
  - **B2**: + graphify MCP on code + 81 English ADRs + 14 reference docs (+813 nodes, Haiku
    semantic pass).
  - **C**: BM25 over the files, no agent.
- Scoring: automatic (expected files and ADR numbers named in the answer's `FILES:` and `ADR:`
  lines), plus a blind Haiku judge (0–2 against the reference, arm hidden, order shuffled).

## Results (60 runs per arm)

| | A (grep/read) | B (code graph) | B2 (code + docs graph) | C (BM25) |
|---|---|---|---|---|
| Locate: file recall | 0.85 | 0.88 | **0.92** | 0.25 (top 5) |
| Locate: file precision | **0.68** | 0.70 | 0.55 | — |
| Why: right ADR cited | **1.00** | 0.78 | 0.92 | 0.92 (top 5) |
| Trace: file recall | 0.64 | **0.75** | **0.75** | 0.00 |
| Trace: mean time | 52 s | 36 s | **31 s** | — |
| Judge score /2 (all) | **1.75** | 1.72 | 1.72 | — |
| Agent cost (60 runs) | 0.39 $ | 0.41 $ | 0.39 $ | 0 $ |

What the numbers say:

- The graph helps a little to **find more files** on traces (+11 points of recall) and makes
  those runs faster, at the price of **less precise answers**, since it brings up more
  neighbours.
- On *why* questions the code graph **hurts**: the agent spends its turns in the graph, where
  the decisions are absent. Adding the docs to the graph brings it back to 0.92, still below
  plain grep (1.00), because grep finds an ADR by its words.
- Overall correctness, judged blind, does not move. With 3 repetitions, a 3-point gap is noise.

## Cost and friction of the graph

- **Code-only graph**: free, deterministic, 2 335 files parsed in a few minutes. Fine.
- **Docs graph**: 1.5 M tokens in, 0.5 M out (Haiku) for 95 documents: **≈ 1.5 $**, about
  20 min. Two ADRs produced 63 k output tokens.
- It failed three times before working:
  - `claude-cli` chunks timed out at 600 s;
  - the evaluation's per-call budget cap (0.20 $) cut every chunk at ~0.27 $, which wasted 1.34 $;
  - the evaluation's budget shim recursed into itself and saturated the machine's memory
    (`scripts/claude_shim.py` now refuses to). That one is the harness's error, not graphify's.
- The full documentation (468 files) would have cost ~10 $ on its own.
- The authors' own code benchmark is n=6. An independent one (issue #4341, 177 SWE-bench
  issues) found graphify's query below plain BM25 at ranking the file to change; this
  evaluation agrees in spirit.

## For choregos's knowledge bases (ADR 0051)

Nothing to take. A graph of the code does not make the project easier to understand for an
agent that can already grep. The decisions (the *why*) live in prose, which the hybrid search
of an `index` base already serves. If a structural view is wanted later, the cheap part (the
deterministic AST graph, 0 $) is the only part worth a second look, and only as a *navigation*
aid for traces, measured against this same question set.

## Limits

- Haiku only. A stronger model might use the graph better, or need it less.
- One repository, 20 questions, 3 repetitions.
- The graphify PreToolUse hook was not installed: it writes to the user's global Claude
  settings. The arms used the MCP server plus an instruction to use it.
- The graph was not refreshed between runs, so the corpus was static.

## Files

- `questions.json`: the 20 questions and their ground truth, frozen before the first run
  (`questions.sha256`).
- `scores.json`: one row per agent run (recall, precision, cost, turns, time, judge score);
  `armC.json`: BM25's top 5 per question.
- `scripts/`: `run_arm.sh` (one agent run, `EVAL_DIR` names the working directory),
  `bm25.py`, `score.py`, `judge.py`, and `claude_shim.py` (the budget cap in front of graphify's
  `claude-cli` backend). The raw transcripts (`runs/`, `judge/`, 1.6 MB) and the corpora were
  left out: the corpus is `git archive 56a39e7e`.
