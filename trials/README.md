# Trials

Each folder is a trial: a question asked of the platform, the scripts that played it, and a
`RESULTS.md` that says what was observed, what it proves and what it does not. The results are
recorded as they were found; a trial is not re-run when a page is edited.

- [`trials/python/`](python/RESULTS.md) - which Python for the monorepo? The low-code engine taken
  over from graal needs 3.13 and Choregos was pinned to 3.12; both suites were run on both versions,
  and the answer is a single version, 3.13.
- [`trials/ontology/`](ontology/RESULTS.md) - can the core carry the ontology and the action engine
  of the future foundation through its seams, without a fork? Elements 1 to 6 on the IT4IT slice:
  a validated package, synchronised findings, generated MCP tools, a proposed action, a
  re-authenticated approval, and a proof by the collector's re-run.
- [`trials/lakehouse/`](lakehouse/RESULTS.md) - element 7: an Iceberg REST catalog (Lakekeeper) on S3
  storage, written by PyIceberg and read by DuckDB with credentials limited to one table (STS), then
  RustFS in place of MinIO.
- [`trials/no-secret/`](no-secret/RESULTS.md) - element 8: a run that receives its token and secrets
  with no Kubernetes Secret, through a single-use bootstrap code and a static binary, on kind.
- [`trials/local-start/`](local-start/RESULTS.md) - element 9: the integrated stack started by
  `docker compose up`, its start-up time and memory, and the IT4IT scenario played against the running
  stack.
- [`trials/dev-platform/`](dev-platform/README.md) - the foundation trial on the dev tenant: a real
  agent, a real PR and a re-authenticated human decision. It has a README and no results page of its
  own.
- [`trials/claude-integration/`](claude-integration/RESULTS.md) - S15-07: a real Claude Code connects
  to the dev tenant's MCP door, lists the tools and opens a work item, checked over REST.
- [`trials/hr/`](hr/RESULTS.md) - S20-07: the `joiners-leavers` template on the dev tenant, with the
  fakes served by one pod, a real agent and real human decisions.
- [`trials/dev-project/`](dev-project/RESULTS.md) - S21-25: the `github-software-delivery` template
  on the dev tenant with a sandbox repository; prepared, not yet played.
- [`trials/investment/`](investment/RESULTS.md) - S38-13: the free market-data sources called for
  real, and the cross-check between two of them.
- [`trials/graphify/`](graphify/RESULTS.md) - S44-03: does a code graph make the project easier for an
  agent to understand? Three arms, blind judge, recorded questions and scores.
- [`trials/knowledge/`](knowledge/example-kb.yaml) - a knowledge base as code about a fictitious
  company, to apply with `choregos knowledge apply -f`. It has no results page.
- [`trials/demo/`](demo/prepare_the_demo.py) - recreates a fresh English demonstration organisation
  with one command, by replaying the HR and development scripts. It has no results page.

The scan `tests/trials/test_trials_in_english.py` keeps this tree English.
