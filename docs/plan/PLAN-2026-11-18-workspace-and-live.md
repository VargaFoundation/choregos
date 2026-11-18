# Plan — 2026-11-18: the workspace is one screen, the flow is live (stream S67)

Source: the product owner's review of `choregos chat` on the production instance, 2026-11-18, and their go the
same day (*"do something clean, follow your recommendations; I also want a live mode, for the CLI and the web,
where I can see the workflow processing the items — something stylish"*). Decision:
[ADR 0187](../adr/0187-the-workspace-is-one-screen-and-the-flow-is-live.md). Stories S67-18 to S67-28, added
to S67 by the amendment of 2026-11-18 of [ADR 0180](../adr/0180-one-quarter-of-depth-the-scope-is-frozen.md).

## 0. What the review found

| Seen | Cause | Story |
|:--|:--|:--|
| Heavy rendering, "Say something" in a frame | five framed panes at once, the composer a sixth frame, the cost in a border | S67-21 |
| `✗ failed` with no reason; the gateway's raw JSON as a decision's title | `ActivityEntry` carries no reason; a pending item's title is printed whole | S67-19, S67-20, S67-22 |
| No way to see every project | the tree lists only projects with a run in the last hour | S67-23 |
| `cost this month (€)` beside `$` amounts | each command picks its own currency | S67-25 |
| FON-319 failed twice | the runner's loop guard (finding #1869), then the item's budget | outside this plan |

## 1. The terminal, after

```
 diametral · diametral-guide                     1 running · 11 waiting for you · €3.80 today
 Conversation   Activity   Projects   Decisions 11   Artefacts   Live              ctrl-k
 ──────────────────────────────────────────────────────────────────┬──────────────────────
 you      iris is not reachable by everyone…                       │ FON-319 · iris
                                                                   │ ● implement  3m34 €0.52
 guide    I propose to start "Make Iris reachable by everyone".    │   ✓ review conventions
          ▸ Confirm  ▸ Refuse                                      │   ● remove the gate
                                                                   │ ✗ implement-1  loop
 › _                                                       €0.002  │ ✗ implement-2  budget
```

## 2. The live view, after

```
 iris · diametral-dev                                                  LIVE ● 14:58:12
 triage 2      refine 1       plan 1        implement 3       review 2       done 41 (+3 today)
 ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌──────────────┐  ┌──────────┐
 │ FON-322 │   │ FON-321  │   │ FON-320 │   │ ◐ FON-319    │  │ FON-310  │
 │ waiting │   │ ◐ 1m12   │   │ ✓       │   │ developer    │  │ ⏸ approve│
 └─────────┘   └──────────┘   └─────────┘   │ 3m34 · €0.52 │  └──────────┘
                                            └──────────────┘
 14:57:32  ✗ FON-319 implement-2  budget spent      14:52:18  ● FON-319 implement started
```

On the web the same lanes are full-height columns, cards slide to their next lane, a running card pulses in
the workflow's hue and its spend ticks; the wall mode drops the navigation and enlarges the type.

## 3. Order

1. S67-18 (this plan), then S67-19 → S67-20 (the contract, then the service).
2. S67-21 (the screen) and S67-26 (the reduction) in parallel; S67-25 at any time.
3. S67-22, S67-23, S67-24 on S67-21; S67-27 on S67-26; S67-28 on S67-21 and S67-26.

Each story is one pull request; its STATUS row says what it proves and what it does not.

## 4. What this plan does not do

- No new route and no new write (ADR 0187 §5).
- The runner's loop guard (finding #1869) is fixed outside this plan.
- The editor extension (S68) stays frozen.
