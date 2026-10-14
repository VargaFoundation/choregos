# 0046 — English everywhere: code, identifiers, plan and commit messages too

- **Status**: accepted, 2026-10-12 — supersedes §2 and §3 of
  [ADR 0039](0039-tout-ce-qu-un-utilisateur-lit-est-en-anglais.md)
- **Concerns**: every file of the repository and every commit — code, identifiers, comments,
  docstrings, tests, file names, `docs/` including `docs/plan/`, ADRs, commit messages and pull
  request descriptions; decision D2 of the plan of 2026-10-12 (item 13)

## Context

[ADR 0039](0039-tout-ce-qu-un-utilisateur-lit-est-en-anglais.md) made English what a *user* reads,
and kept two things in French on purpose: identifiers (§2: state names, field names, machine codes
are keys) and the code itself, its comments, `docs/plan/` and commit messages (§3). The repository
is now read by people who do not read French — contributors, reviewers, the customers of the
enterprise edition who audit what they run — and by agents whose instructions, plans and status
pages were half in one language and half in the other. Keeping the code in French also kept a
translation step between every English reference page and the code it describes.

Decision D2 (Vincent Devillers, 2026-10-12): everything is in English, identifiers included.

## Decision

1. **Everything new is in English**: identifiers, comments, docstrings, test names, file names,
   `docs/` including `docs/plan/` (STATUS, BLOCKERS, plans), ADRs, commit messages, pull request
   titles and descriptions. `docs/fr/` stays an unmaintained archive.
2. **Identifiers are renamed too** (reversing ADR 0039 §2) — by a mechanical rename, **one pull
   request per package** (S34-04), scheduled where no feature stream touches that package. Comments
   and docstrings are translated in the same pass. Until a package's turn comes, its French code
   is not mass-translated by unrelated stories; a function a story rewrites may be renamed if no
   contract or Temporal name depends on it.
3. **Surfaces that would break are handled one by one, never by the mass rename:**
   - **`workflow.patched` ids are never renamed.** A patch id is recorded in the history of every
     running workflow; renaming it changes which branch a replay takes and breaks determinism. Each
     one keeps its French id, with a comment saying so.
   - **Temporal activity and signal names** get English names, and the French names stay
     registered as **aliases** until no running workflow uses them; recorded histories replay with
     both (S34-02).
   - **OpenAPI properties and error codes** (`cible`, `cout_outils_eur`, `demandes`, `refus`;
     `gate.sans_matiere`, `workflow.effet_implicite`) change through one `contract-change` pull
     request, the old names accepted for one release (S34-03, ADR 0001).
   - **Workflow template keys** (joiners-leavers: `a_revoir`, `clos`, `date_depart`…) change in a
     new template version; installed workflows move by the existing workflow migration path.
   - **Log event names** change together with the alerts that query them.
   - **ADR and plan file names** are renamed with redirects, links checked by the docs test (S34-05).
4. **Git history is not rewritten.** Past commit messages stay as they are.
5. **A guard refuses French in new or modified lines.** `tools/check_english.py` reads the lines a
   change adds — comments, docstrings and strings in Python and TypeScript, JSX text, Markdown
   outside code blocks, YAML values and comments, Makefile comments — and the commit messages of
   the branch. It runs in `make lint` and in the `english` job of CI on every pull request. Its
   detector extends the one of `tests/langue/test_anglais.py`: French-only accents (proper names
   and loanwords aside), a French lexicon, French contractions, French function words. A line that
   must stay French — a patch id, an alias, a test sample of French — carries `lang: fr-ok` in a
   comment on the same line.

## Consequences

- AGENTS.md (`CLAUDE.md`) says English everywhere; `docs/CONTRIBUTING.md` and `docs/README.md`
  say the same.
- For a while the repository is mixed: untouched files stay French until their package's rename.
  The guard reads only added lines for that reason; a guard failing on every untouched line would
  be switched off on the first day.
- A modified line that calls an existing French function is not flagged: identifiers are not
  prose. A sentence that names one puts it in backticks.
- The guard is a heuristic. Its known blind spots: a French sentence without accents, lexicon
  words, contractions or two function words; a single unaccented French word. Its false positives
  on English are settled by its lists of proper names and loanwords, or by the escape, never by
  excluding a path — the excluded paths (the archive, generated files, the injection guard's
  French patterns) are listed in the tool with their reason.
- ADR 0039 §1 (what a user reads is English) and §4 (its shipped-text guard) stand.

## Alternatives discarded

- **Keep ADR 0039 §2-3** (French code, English surfaces): it keeps every reader of the code, and
  every agent, translating between the reference pages and the code they describe.
- **Translate everything in one pull request**: the largest change of the plan by line count, in
  conflict with every other stream at once, and unreviewable.
- **Rename patch ids and Temporal names with the rest**: breaks the replay of every running
  workflow; aliases and frozen ids cost a comment each.
- **Rewrite git history**: breaks every clone, fork and signed commit for no reader's benefit.
