# 0198 — Procurement is a plugin whose code half is a job image

- **Status**: proposed, 2026-11-20 (written by story S84-02 from the product owner's request of the
  same day; accepted when the owner merges it)
- **Concerns**: a new plugin `procurement` in the agents repository (content) and in
  `plugins/procurement/` here (the distribution `tranibor-procurement` and its job image); applies
  [ADR 0181](0181-the-contracts-speak-no-trade.md) (a trade ships a plugin),
  [ADR 0056](0056-a-deterministic-step.md) (a deterministic step and its measured facts),
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md) (a plugin's server) and
  [ADR 0035](0035-governed-actions-in-the-core.md) (governed writes); builds on
  [ADR 0197](0197-a-person-hands-files-to-a-work-item.md) (the files a person hands); stories S84-07
  to S84-14 and the stories `PA-01` to `PA-11` of
  [the plan of 2026-11-20](../plan/PLAN-2026-11-20-procurement.md)

## Context

The product owner asked on 2026-11-20 how the platform helps the purchasing departments of companies
adopt agents. Procurement is a trade of controls: signature thresholds, separation of duties, an audit
trail, due diligence on suppliers. What holds a buyer back from an agent is not what it can write but
what it can be trusted with: a comparison of offers that quotes a price no supplier wrote is worse than
no comparison.

The platform already carries the mechanisms that answer this. A deterministic step whose facts are
measured (ADR 0056). A gate, `numbers_measured`, that refuses any number of an output that no measured
document holds, and can force the numbers of a document into the text. Human steps answered by a
group, with an attestation. Governed writes to a third-party system, approved by a re-authenticated
person. A fan-out with a join (ADR 0057). A ledger that gives the cost of one work item.

ADR 0181 settles where a trade lives: in a plugin, and nowhere in the core. Its two guards refuse a
trade's word in the contracts, the frozen surfaces and the core's packages. `investment-research` is
the one precedent of a plugin with a code half; it needed an extension (a connector family) and a job
image.

Two properties of this trade shape the decisions below. Its raw material is **documents written by a
counterparty with an interest in the outcome**: untrusted by nature, and confidential. And its
documents are **European as often as not**: an amount is written `12 500,50` as readily as
`12,500.50`, and `numbers_measured` reads the second notation only.

## Decision

1. **One plugin, `procurement`, in two halves, and no extension.** Its content (agents, skills, the
   template `procurement-sourcing`, evals, procedures, later a server and an assistant) is published
   from the agents repository. Its code is the distribution `tranibor-procurement`, in
   `plugins/procurement/` of this repository, whose only product is a command that job steps run. The
   plugin registers nothing through the `choregos.plugins` entry point: it brings no gate, no
   connector type, no route and no table, and the API and the workers never import it. It uses the
   core's gates as they are. If it comes to need a gate of its own, that is a new decision, taken
   then.
2. **The job image has its own environment.** The image `procurement` is built FROM the release's
   runner image, like the `investment-research` one, so the runner inside always matches the platform.
   Unlike it, the command has dependencies (document conversion). They are installed in a virtual
   environment of their own, from a hash-pinned export of the lock file, and the command is linked on
   the path. The runner's environment stays byte for byte the release's.
3. **Extraction is a deterministic step, and it normalises numbers.** `extract` converts every handed
   file to text in a child process with a timeout, with the libraries the API already pins for
   knowledge bases. Per file, it decides the number notation from the amounts it finds and rewrites
   decimal-comma amounts to decimal-point form, so that the text a gate reads is in the one notation
   the gate knows; a file it cannot decide is left as written and counted. A file with no text layer,
   an encrypted file, a conversion that fails or times out is counted in a measured fact, and the
   workflow's gate refuses any: such a file goes to a person. **There is no OCR.**
4. **Agents never compute.** In the comparison of offers, one agent copies what the offers say into a
   structure, and a gate checks every number of that structure against the extracted text. A job
   multiplies, adds, compares and ranks, in decimal arithmetic. A second agent writes the memo from
   the job's numbers, and a gate checks every number of the memo against them and forces the totals
   and every arithmetic error the job found into the text. The memo ranks by measured price and lists
   what differs. **It does not recommend**: the award is a person's, in an attested task.
5. **A threshold is an argument in the workflow file, never a field.** The amount from which a
   consultation goes to a committee is written in the command of the step that derives the approval
   tier. A field can be edited by the person the threshold constrains; a workflow version is pinned
   to the work item when it starts. The step fails without its threshold: no default sends work to
   the lighter tier.
6. **The first version writes nothing outside the platform.** The comparison of offers has no
   governed action. Writes come with the plugin's server.
7. **One server, `purchasing`, named after what it does.** The plugin's MCP server serves a fixed
   profile of operations (suppliers, framework agreements, contracts, purchase requests) from a
   backend chosen by configuration. Workflows name the connector `purchasing` and the profile's
   operations, so a second purchasing system is a new backend of the same server and no workflow
   changes. Every write takes `human_approval: true` and a compensation; filters are typed, never a
   backend's raw query. An operation a backend cannot serve honestly answers that it is not
   supported.
8. **Bank details are out.** No operation of the profile reads or writes one, and a supplier record
   is created without payment data. Changing a supplier's bank account is the classic fraud of this
   trade and stays a human process.
9. **Supplier documents are untrusted and confidential.** The plugin's agents declare no web tool, no
   catalogue tool and **no memory**: an offer must not reach another consultation through a shared
   memory. The template's policy is the closed sandbox. Files reach a step as files (ADR 0197). Each
   agent's evals carry a document with an injected instruction, which the agent must report and not
   follow.

## What a reader must know

- **No OCR.** A scanned offer is not read; it stops the work item at a person.
- **No injection detector.** The protection is mechanical: nothing is written outside the platform
  by the first version, every later write waits for a re-authenticated person, the agents hold no
  credential and no network. An instruction hidden in an offer can still bend what a memo says; the
  number gates bound what it can make the memo claim, and a person reads the memo.
- **No write to a third-party system** before the server ships, and never a bank detail.
- **Measured does not mean true.** A number is measured when a job computed it from what an agent
  copied and a gate matched against the extracted text. A supplier's own mistake is copied faithfully,
  and reported when it is arithmetic.

## Consequences

- The core gains no word of the trade. The two guards of ADR 0181 are untouched.
- A second distribution lives under `plugins/`, with a second job image that each release builds,
  signs and publishes. The pattern is now used twice and may be worth a seam of its own
  (a plugin declaring its job image) when a third comes.
- The first proof of the plugin needs no purchasing system: a person, three files and a model.
- The plugin's server will be proved against real software that anyone can start in a container. The
  backends of the large purchasing suites need a sandbox that only their customers obtain, and follow
  the first organisation that runs one.

## Rejected alternatives

- **An extension with a gate of its own** (for instance "the award is the lowest bid"). The core's
  gates suffice, and a rule of that kind is an organisation's policy, not the plugin's.
- **A generic `documents` job image in the core.** Extraction is not a trade's, and a second plugin
  will want it. But it means moving the knowledge converter out of the API, a refactor on nobody's
  critical path. Reopened when a second plugin needs to read documents.
- **Let the agent read the files and compare.** Nothing would be measured: the number gate needs a
  document the platform stored from an earlier, deterministic step.
- **A weighted score of qualitative criteria, computed from an agent's marks.** A mark given by a
  model is a judgment dressed as a measure. The memo states the differences in prose and a person
  weighs them.
- **A server per vendor.** Each workflow would name a vendor, and every organisation would fork the
  template.
- **Currency conversion.** A rate is a fact with a date and a source the platform does not hold;
  offers in several currencies stop at a person.
