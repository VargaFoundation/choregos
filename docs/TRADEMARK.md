# Trademark and naming policy

Apache-2.0 grants copyright and patent rights. It grants **no trademark rights** — section 6 says
so explicitly. This page says what we do with that, because two entities publish software under
this name ([ADR 0024](adr/0024-two-editions.md)) and an unwritten policy is one that gets tested
by the first person who disagrees with it.

"Tranibor" and the Varga Foundation name are the marks of the Varga Foundation. The project was named
Choregos until 2026-11-17 ([ADR 0176](adr/0176-the-core-is-named-tranibor.md)). The enterprise edition is
published by a third party, GraalSystems (a DiametralGroup company), and operated by DiametralGroup
companies, **under its own product name, the graal Agentic Platform**, which is built on Tranibor. The
edition announces its own publisher (`GET /edition`, fields `publisher` and `edition_version`) and the
console shows it; the community edition announces none.

## What you may do, without asking anyone

- **Use** the software, for anything, including commercially. That is the licence, not a favour.
- **Fork** it, modify it, redistribute it under Apache-2.0.
- Say truthfully that your product **works with**, **is built on**, **is compatible with** or
  **is a fork of** Tranibor. Factual statements about interoperability need no permission.
- Use the name in documentation, talks, articles, comparisons and job adverts.
- Keep the name in an unmodified redistribution (a container image mirror, a distribution package).

## What needs permission

- Naming a **product or service** so that it reads as ours: "Tranibor Enterprise", "Tranibor
  Cloud", "Tranibor Pro", `tranibor.<anything>` or `choregos.<anything>` as a commercial domain, or a logo that borrows the
  mark's shape.
- Presenting a **modified** build as Tranibor. Fork freely, but a fork that behaves differently
  under the same name makes our bug reports and our security advisories wrong.
- Using the mark in a way that suggests the Varga Foundation **endorses**, certifies or supports
  you.

## Why this is written down and not merely assumed

Two editions share one core. If anyone may ship "Tranibor Enterprise", then the phrase stops
telling a buyer what they are buying — and the first casualty is the audit trail this product
exists to produce: "which Tranibor produced this attestation?" must have one answer.

The mark is **not yet registered** (classes 9 and 42 are listed as to-do in
`docs/plan/00-index.md`). Until it is, this page is a statement of intent and the licence is what
is enforceable. Saying so is more useful than implying a protection that does not exist yet.

## Questions

Open an issue. There is no lawyer to route you to yet, and pretending otherwise would waste your
time.
