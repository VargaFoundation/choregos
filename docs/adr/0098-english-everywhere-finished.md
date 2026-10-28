# 0098 — English everywhere, finished: a per-area ratchet, aliases with a window, and the names that stay frozen

- **Status**: accepted, 2026-10-27 — completes [ADR 0046](0046-english-everywhere.md); the plan of
  2026-10-27 (stream S45) carries it out
- **Concerns**: every tracked file and every identifier — code, tests, file names, `docs/`,
  `docs/plan/`, charts, trials; `tools/check_english.py`, `tools/english_baseline/`, `make lint`,
  the `english` job of CI; the extension API and the other names an outside package or an operator
  uses; the Temporal histories and persisted rows that record French names

## Context

[ADR 0046](0046-english-everywhere.md) made everything new English and renamed the existing French
package by package (S34-04). Its guard reads only the lines a change **adds**, so it stops new
French but never measures what is left, and a package already translated can take French back
through a line moved, restored or merged from an old branch. What is left, measured on 2026-10-27:
about 11,600 French prose lines; thousands of French identifier declarations (the API's, the
web's, the adapters', the orchestrator's, the core's, the runner's); about 400 French-named
paths (API modules, migration files, web files, `essai/`, `tests/langue`, `tests/paquets`,
runbooks); a `docs/plan/STATUS.md` that is mostly French; a root README that says the reference
documentation is in French.

Finishing is a large change that touches every package, and some French names cannot be renamed:
they are recorded in the histories of running workflows, persisted in rows, published in a plugin
API that another repository imports, or named in an alert a deployed cluster already fires. A
mass rename in one pull request is unreviewable and breaks all of them at once; leaving the
count unmeasured lets it grow back. The decision below measures first, renames in file-disjoint
stories, keeps every name that cannot move behind an alias with a window, and freezes the rest.

## Decision

1. **Measure first: a ratchet, not a promise (S45-01).** `tools/check_english.py --all` counts,
   for every tracked file, three things: the French **prose** lines (what the guard of ADR 0046
   reads: comments, docstrings, strings, Markdown, YAML values, shell, SQL, Dockerfile, CSS and
   Rust comments, HTML and Jinja text), the **distinct French names the file declares** (Python by
   its syntax tree — functions, classes, parameters, names and attributes stored to, `import … as`;
   TypeScript by its declaration keywords — never a name that is only used) and whether a
   **component of its path** is French (judged whole, extension included). A name is French when
   one of its words is in the identifier lexicon, or at least two distinct French function words
   are; tokens that hold a digit are dropped, camelCase is split. The counts are stored in
   `tools/english_baseline/<area>.tsv`, one file per area (23 areas, the longest prefix wins) so
   that no two stories write the same file. A count can only go down: a file above its baseline,
   or a new file with French, fails `make lint` and the `english` job; a stale entry (a count
   below its baseline, a file that is gone) only prints the command that lowers it, until the
   ratchet is made strict at zero (§8). It replaces the `ENGLISH` and `STILL_FRENCH` lists of
   `tests/langue/test_english_packages.py`. Three escapes exist: `lang: fr-ok` on a line (a name
   or a sentence that must stay French), `lang: fr-ok-file` in the first five lines of a file (a
   compatibility shim or a redirect stub), and `FROZEN_PATHS` in the tool (paths whose French
   stem is a recorded workflow id), each with its reason.
2. **Serial enablers come before any mechanical rename.** The renames below are safe only after:
   the contract change that gives the access report and the error codes English names (the old
   keys kept for one release); **dual-read** of every French key or error type recorded in a
   Temporal history or persisted in a row (readers accept both names first, writers switch one
   release later); the extension API in English with deprecated French aliases (the helper
   `choregos_core.compat`); environment variables through `AliasChoices`; `python -m` and
   console-script shims.
3. **Alias policy.** A rename keeps the old name working, by the rule that fits what was renamed.
   Every alias line carries `lang: fr-ok alias:internal` or `lang: fr-ok alias:external`, and a
   shim file carries `lang: fr-ok-file alias:internal` or `alias:external` in its header.
   - **(a) A module.** `git mv` the module, translate it, and re-create the old path as a shim:
     the SPDX line, the header, explicit imports of the English names (never `import *`, never
     `from x import New as Old`), one typed `Old = New` per former name, and
     `replace_module(__name__, sys.modules['new.module'])` when the old dotted name must stay
     importable.
   - **(b) A name.** A renamed name that anything outside the story's own files imports keeps a
     typed alias in the module that defined it: `Old = New  # lang: fr-ok alias:internal`. Find
     the importers with `git grep -nw OLD -- apps packages tests`. A name used only inside the
     story's own files gets no alias: it is renamed everywhere at once.
   - **(c) A name that tests monkeypatch.** A plain `Old = New` does not forward a patch. The
     module calls `choregos_core.compat.alias_names(__name__, {'Old': 'New'})` once, at its end:
     reading, patching and undoing the old name reach the new one, under strict mypy. Never a
     module `__getattr__` (it gives `Any` to strict mypy), and never in a module under
     `apps/orchestrator/src/choregos_orchestrator/workflows/` (the Temporal sandbox re-imports it).
   - **(d) A member.** A renamed method keeps `old = new` in the class body; a renamed field keeps
     a property with a getter, and a setter when the field is written; a pydantic field read from
     payloads keeps `Field(validation_alias=AliasChoices('new', 'old'))` with `populate_by_name`.
   - **(e) A keyword.** A renamed keyword argument is also accepted under its former name, as a
     keyword-only parameter defaulting to `None`, on a line of its own that carries the alias
     marker. An external former keyword emits a `DeprecationWarning`. Positional callers are not
     affected.
   - **(f) An external name** — the public extension API, environment variables, entry points,
     deprecated contract fields, alert names (through a `legacy_alertname` label), runbook URLs
     (through redirect stubs), the YAML key of the tool catalogue — keeps an `alias:external`
     window of one release. It warns with the release that removes it (`EXTERNAL_ALIAS_REMOVAL`,
     0.22.0), has one removal issue (label `deprecation`, milestone 0.22.0) and, when another
     repository imports it, a coordination issue listing every pair.
   - **(g) A test module is never shimmed.** It is moved with `git mv` and no redirect; a helper
     that several test modules import moves to a kit module that they all import.
   - **(h) A story renames what it owns and switches no importer it does not own.** The importers
     of a former name keep resolving through (a) to (e) until their own story switches them. A
     story uses the English name another story chose only where that story has merged: it reads
     that story's rename table in `docs/upgrade-notes/0.21/<story-id>.md`, not the plan.
4. **Frozen forever.** `workflow.patched` ids; activity and signal wire names (the French name
   stays registered as an alias); error-type strings and payload keys recorded in histories; the
   contents of the recorded history files and the four history files whose stem is a recorded
   workflow id; Grafana dashboard uids (no alias exists). Persisted French JSON values are read
   under both names and no row is rewritten. A line that carries one of them says why:
   `lang: fr-ok` and the reason, in a comment. The list, as recorded on 2026-10-27:
   - recorded error types: `AgentIndisponible`, `InstructionsInvalides`, `SkillAbsente`,
     `EffetRefuse`, `ActionIntrouvable`, `ActionPasEncoreVisible`;
   - recorded payload keys: `effets`, `statut`, `etats`, `attendre_une_preuve`, `jusqu_a`,
     `en_attente`, `preuve`, `conclure`, `cle`, `refus` (the result of
     `propose_transition_action` included) and `TrainInput.seuil_de_relais`;
   - persisted keys and values: `events.payload.cle`, `actions.params.ontologie`,
     `actions.result` `attente`/`preuves`, the effect kinds `ontology.effet`/`ontology.preuve`, the
     run-event key `demarre`, the audit keys `jeton`/`ecriture`/`avant`/`apres`/`sans_greffon`, the
     audit value `issue='refus'`, the cost-ledger provider prefix `greffon:` and the extension tool
     key `ecriture`;
   - the inputs and algorithms of the agent catalogue digest and of `agent_versions.checksum`.

   Readers accept both spellings first (S45-03); writers switch one tagged release later
   (S45-39), on the workflow side behind the patch `english-payload-keys`.
5. **Package stories are file-disjoint**, split by sub-area, each no larger than L. Files that
   another stream owns (the MCP client path) are excluded from every slice. Renames in `apps/api`
   run after the security stories that share its configuration and entry-point files.
6. **Archives are deleted** once nothing reads them: `docs/fr/`, the French ADR stubs and the
   French plan pointers, after every link and every test that counts or follows them is fixed.
   A runbook rename keeps a three-line redirect stub, because the `runbook_url` of an alert that a
   deployed version fires points at the old name.
7. **Vocabulary.** A package declared in the `choregos.plugins` entry-point group is an
   **extension**; "plugin" means the package of [ADR 0093](0093-an-agent-ships-as-a-plugin.md)
   (Decision 1). The English name of what the French code called `greffons` is therefore
   `extensions` (`choregos_api.extensions`, `load_extensions`, `EXTENSION_GROUP`), never
   `plugins`. The entry-point group value `choregos.plugins` and persisted prefixes never change.
8. **Order and windows.** Internal aliases are deleted when the last importer is switched (the
   stories that close each package) and in any case before the release that ends the track;
   external aliases are removed in 0.22.0, one story each. Readers of a recorded name ship before
   its writers (one release apart). After every wave the integrator regenerates the baselines
   with `--all --update-baseline` and checks that no area count rose. When the baselines hold
   only their headers, `--all` fails on a stale entry and a test asserts that every baseline is
   empty, and AGENTS.md says that there is no French left in the repository.
9. **The baseline recipe.** Every story that renames follows it, and none commits a baseline
   file (the integrator owns them after S45-01, S45-47 excepted):
   1. Before the change, `uv run python tools/check_english.py --all --report` and keep the
      totals of the areas the story owns.
   2. Rename and translate the story's files. A line that must stay French carries
      `lang: fr-ok` (a name or a sentence) or its file carries `lang: fr-ok-file` (a shim): never
      leave it counted by omission.
   3. `uv run python tools/check_english.py --all` must exit 0: no file above its baseline.
      A rename that raises another file's count (an importer that now holds a French alias
      line, a new French path) is fixed, not baselined.
   4. `uv run python tools/check_english.py --update-baseline --area <area>` for each owned area,
      to see what dropped: `git diff tools/english_baseline` shows only lowered or removed lines,
      and none of the story's owned files remain when the story says so. It refuses a rise.
   5. `git checkout -- tools/english_baseline` before committing; the pull request body gives the
      per-area totals before and after (`--report`).
   6. An English word the lexicon flags is a bug of the lexicon: remove the word from it and say
      so in the pull request. Never edit the English file to dodge the lexicon.
10. **What the ratchet proves, and what it does not.** It proves that no file got more French
    than the baseline says, in the three passes, on every pull request. It does not prove the
    lexicon knows every French word: a French word it has never met, a French sentence without
    an accent, lexicon word or two function words, and a French name made of unknown words pass
    (the sweep of these blind spots is S45-45). A false positive on English is settled by the
    lexicon or the escape, never by excluding a path: the excluded paths are listed in the tool
    with their reason.

## Consequences

- `make lint`, `make ci` and the `english` job of CI fail when French rises anywhere in the tree,
  not only on the lines a change adds; `make english-ratchet` runs the same check alone.
- The shipped-text detector of [ADR 0039](0039-everything-a-user-reads-is-in-english.md) §4 moved
  into `tools/check_english.py` (`looks_like_shipped_french`); the guards of the playbooks, the
  API errors and the orchestrator load it from there, and `tests/langue/test_anglais.py` keeps
  its former name as an alias.
- Four stems of replay histories stay French by `FROZEN_PATHS`; every other French name has an
  English name, an alias with a window, or a reason on its line.
- Operators get one table per story of the names that changed and what to do about each, in
  `docs/upgrade-notes/0.21/`; the folder is folded into `docs/deployment.md` at the end.
- The ratchet is as good as its lexicon. Its numbers are a floor of what is French, not a count
  of it.

## Alternatives discarded

- **One mass-rename pull request**: unreviewable, in conflict with every other stream, and it
  breaks every recorded name at once.
- **PEP 562 `__getattr__` aliases**: strict mypy gives `Any` to what a module `__getattr__`
  returns, so a former name would lose its type.
- **One shared baseline file**: every story would write it, so every story would conflict.
- **Rewriting histories or persisted rows**: breaks replay and rolls back nothing; reading both
  names costs a few lines.
- **Renaming Grafana dashboard uids**: no alias exists for a uid, so every saved link and
  provisioned dashboard would break.
- **Dual Kyverno rules**: the policies are renamed with an upgrade note instead.
- **Failing on stale entries from the start**: a count below its baseline is progress, and a
  guard that fails on progress is switched off; it turns strict when the baselines are empty.
