# Trial, element TRV-009 - Python 3.12 or 3.13? Results of 2026-10-03

**Question** (Q14 of the requirements document): the low-code engine taken over from graal requires Python >= 3.13,
Choregos is pinned to `>=3.12,<3.13`. Is a separate service needed for the engine, or a single version?

**Answer: a single version, 3.13, for the whole monorepo.**

| Suite | Python 3.12 | Python 3.13 |
|---|---|---|
| graal's low-code engine (`lowcode/`, unit tests, no Java) | 4,906 passed, 82 skipped (after relaxing `requires-python`) | 4,906 passed, 82 skipped (73 s) |
| Choregos (fast suite, `-m "not slow"`) | 918 passed, 18 skipped | 918 passed, 18 skipped (dependencies resolved afresh) |
| Choregos, `mypy --strict` (214 files) | no error | no error (`python_version = "3.13"`) |

**Method.**
- The engine was extracted from graal (`git archive origin/main lowcode`, revision `35f6eb2f`) into a
  temporary directory, **outside this public repository**: no graal code is added here before the rights
  audit (TRV-040).
- Choregos was tested under 3.13 in a throwaway worktree, with `requires-python = ">=3.12,<3.14"`.

**What follows.**
- The engine's `>=3.13` constraint is not a technical necessity for its unit tests;
  Choregos passes under 3.13 without a code change.
- Recommendation: **Python 3.13 everywhere**, adopted by a structural PR in phase 1
  (`requires-python`, CI, images); the engine comes in as is, without a separate service.
- Not checked: the engine's e2e tests (external services) and Choregos' slow or cluster tests
  under 3.13.
