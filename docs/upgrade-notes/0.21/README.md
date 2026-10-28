# Upgrading to 0.21: names that changed

The English rename of [ADR 0098](../../adr/0098-english-everywhere-finished.md) changes names in
code, configuration and files. Most of them keep working under their former name for one release;
this folder says which, and what an operator does about each.

Each story of the rename writes one fragment, `<story id>.md` (for example `S45-04.md`), with a
table:

```markdown
| Former | New | What an operator does |
| :-- | :-- | :-- |
| `CHOREGOS_OLD_NAME` | `CHOREGOS_NEW_NAME` | Rename it in your values or `.env`; the former one still works until 0.22.0 and warns. |
```

- A row that holds French ends with `<!-- lang: fr-ok -->`, so that `tools/check_english.py` leaves
  the line alone: the former name is French on purpose, it is what the operator has today.
- "What an operator does" says one of three things: nothing (an internal name, an alias keeps
  working), "rename it before 0.22.0" (an external name under an `alias:external` window,
  ADR 0098 §3 (f)), or what to change by hand (a file moved, a key renamed with no alias).
- A name recorded in a Temporal history or persisted in a row is not listed as renamed: it is read
  under both names and never rewritten (ADR 0098 §4).

S45-44 folds this folder into `docs/deployment.md` (the section "Renamed in 0.21") and deletes the
fragments.
