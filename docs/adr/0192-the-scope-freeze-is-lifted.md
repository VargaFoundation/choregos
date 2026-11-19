# 0192 — The scope freeze is lifted

- **Status**: accepted, 2026-11-19 (decided by the project lead; written by story S81-01)
- **Concerns**: [ADR 0180](0180-one-quarter-of-depth-the-scope-is-frozen.md) §1 and §2 (amended),
  `tests/docs/test_scope_freeze.py` (removed), `docs/plan/08-backlog.yaml` (stream S81 opened)

## Context

ADR 0180 froze the scope on 2026-11-17: no stream opened after 2026-11-20 other than S80, and each
stream in flight finished or frozen by name, until 2027-02-28. Among the streams it froze are hosted
agents and voice (S41) and assistants (S63, S66).

On 2026-11-19 the project lead asked for a native Android app, to manage agents and assistants from a
phone and to talk to the voice agents. That is a new stream, and it calls on two frozen ones: the
voice agents are its main use, and the assistants are half of what it shows. Asked whether to keep
the freeze and work around it (an app in another repository, using only the frozen surfaces), the
project lead answered: no freeze, everything is to be delivered.

## Decision

1. **ADR 0180 §1 is lifted.** A stream may be opened in `docs/plan/08-backlog.yaml` again, after
   2026-11-20 as before it. `tests/docs/test_scope_freeze.py`, whose only purpose was to refuse a new
   stream, is removed.
2. **ADR 0180 §2's *frozen* verdicts are lifted.** Every stream that table marks *frozen* (the editor
   extension, assistants, outposts, the Tekton and Azure Container Apps executors, hosted agents and
   voice, investment research, and the others named on its last rows) may take new stories again. The
   *finish* verdicts stand: they are work still owed.
3. **What ADR 0180 decided beyond the freeze stands.** The roadmap's workstreams (S80), the triage of
   the partial stories and its guard (`tests/docs/test_partial_triage.py`), human review of contracts,
   migrations and security guards (§3, `tests/docs/test_human_review.py`), and the two decisions due in
   December (§4) are unchanged. The goal of fewer 🟡 rows is unchanged too; it is now met while new
   streams open, not instead of them.
4. **Stream S81 is opened**: the phone is a surface of the platform
   ([ADR 0193](0193-the-phone-is-a-surface-of-the-platform.md)).

## Consequences

- The cost ADR 0180 named comes back: every new surface adds to what has to be stabilised before 1.0.
  The partial-story count is the measure that says whether that cost is kept in hand.
- ADR 0180 stays accepted for its §3 and §4; its §1 and §2 read as amended by this record.

## Rejected alternatives

- **Keep the freeze and build the app in another repository.** It would respect the letter of
  ADR 0180, but the app needs at least one new server surface (telling a phone that a decision waits),
  and it would leave the voice and assistant streams it relies on frozen.
- **Open S81 alone, by amendment, and keep the other streams frozen.** Not what was asked.
