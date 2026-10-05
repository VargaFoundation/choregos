# 0036 — A template ships what a plugin installs; without the plugin, the project is born without it

- **Status**: accepted, 2026-10-06
- **Concerns**: the template contract (`defaults.extensions`), a plugin seam
  (`declarer_un_installateur_de_gabarit`), project creation; follows
  [ADR 0031](0031-plusieurs-workflows-par-projet.md) (what a template delivers) and
  [ADR 0035](0035-actions-gouvernees-dans-le-coeur.md) (the ontology's actions run in the core)

## Context

A template delivers what the core knows how to install: workflows, their default and routing, a
policy, and — since S20-07 — agents and skills, which enter the organisation. The `joiners-leavers`
template also needs an **ontology**: the register of what an arrival opens and a departure must close
(a person, a contract, an account, groups, a laptop, a badge), readable by an HR person's Claude at
the MCP door.

The ontology is a **plugin**, and a trial one: it is off unless `CHOREGOS_ESSAI_ONTOLOGIE=1`. The core
must not import it, and a template that requires it must not break every deployment where it is off.

## Decision

1. A template names what a plugin installs under `defaults.extensions`: an installer name → a folder
   of the template (`ontology: ./ontology`). The core reads the folder with the same rules as a
   skill — never outside the template, no symbolic link — and knows nothing else about it.
2. A plugin declares an installer under that name, `declarer_un_installateur_de_gabarit(nom,
   installer)`; two plugins cannot claim the same name. The installer runs **in the transaction that
   creates the project**: if it refuses (an invalid package), the project is not born — as with an
   invalid workflow, the template is what is wrong.
3. **Without an installer, the project is born without the extension**, and the audit log says so
   (`template.extension.skip`, with the reason); an installed one is audited too
   (`template.extension.install`, with what the installer returned). An extension is an addition to
   a project, never a condition of its workflows: a workflow of a template calls core effects only.
4. The conformance suite of the templates judges each extension for its plugin before any customer
   does: an ontology compiles without error **or warning**, its effects and evidence are ones the
   plugin serves, and an agent it allows to propose is one the template delivers. An extension name
   the suite does not know fails it.

## Consequences

- `joiners-leavers` ships its register (`templates/joiners-leavers/ontology/`); where the plugin is
  on — the dev environment — an HR project is born with it.
- The register is kept by the ontology's actions, played like any governed action: recording what a
  workflow opened is low risk and approved by policy; declaring someone gone closes their access in
  the register and needs a person, re-authenticated. The workflows do not write to the register yet:
  they act on the directory, the device manager and the badge readers, and a daily reconciliation
  is still to come.
- A template published in the database carries no files: like agents and skills, it cannot ship an
  extension.
