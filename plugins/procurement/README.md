# tranibor-procurement

The code half of the `procurement` plugin ([ADR 0198](../../docs/adr/0198-procurement-is-a-plugin-whose-code-half-is-a-job-image.md)):
a command that job steps run. It registers nothing through `choregos.plugins`, and the API and the
workers never import it. The plugin's content (agents, template, evals) is published from the agents
repository.

## `tranibor-procurement extract`

```
tranibor-procurement extract [--timeout N] [--max-chars-per-file N]
```

Reads the files of `.choregos/inputs/` (the workspace is `TRANIBOR_WORKSPACE`, else the current
directory) and the field `number_format` of `.choregos/fields.json` (`auto`, `decimal_comma`,
`decimal_point`; default `auto`). Writes `.choregos/job.json` = `{summary, facts, outputs}`.

- **Output `offers_text`**: `{files: [{name, sha256, type, pages, chars, notation, text}]}`, the readable
  files by name. `text` is cut at `--max-chars-per-file` (150000); `chars` is its length before the cut.
- **Facts**: `files_total`, `files_read`, `files_unreadable`, `files_truncated`, `notation_ambiguous`.
  The summary names each unreadable file and its reason.
- **Reasons**: `encrypted`, `no_text_layer` (a PDF under 20 characters per page: there is no OCR),
  `timeout`, `converter_error`, `type_mismatch`, `unsupported_type`, `empty`, `not_a_regular_file`.
- **Exit code**: 0 whenever the contract is kept, even if no file could be read (the workflow's gate
  decides); 2 for a broken contract (no inputs directory, a bad `number_format`, an output over the
  inline bound of 2 MiB).

Each file is converted in a child process with a bare environment and a timeout (30 s by default).
PDF, DOCX and PPTX go through MarkItDown, a workbook through openpyxl (cached values, 2000 rows per
sheet), CSV and text as UTF-8 with a fallback to Latin-1.

### Numbers

Per file, the notation is decided from its amounts: `12 500,50`, `12.500,50`, `125,50` are decimal-comma
evidence, `12,500.50`, `12.50` decimal-point evidence. The majority wins; a tie or no evidence is
`ambiguous` and the file is left as written. A decimal-comma file is rewritten to plain decimal-point
form (`12500.50`). Dates, times, phone numbers, identifiers (more than 15 digits, a number glued to
letters, an IBAN-shaped block) and clause numbers at the start of a line are left alone. Known limit: a
space-grouped integer (`10 500`) of a decimal-comma file is read as 10500.

Extraction does not judge: an instruction written in an offer is text like any other, kept unchanged.

## Tests

`uv run pytest -q plugins/procurement`. The fixtures of `tests/fixtures/` come from
`tests/make_fixtures.py` (three invented suppliers); the PDFs are written by hand, the encrypted one with
the standard security handler (user password `secret`).

## `tranibor-procurement compare`

```
tranibor-procurement compare
```

Reads `.choregos/inputs/offers_structured.json` (the shape of
`src/tranibor_procurement/schemas/offers_structured.schema.json`: each supplier has its own `currency`,
every number nullable), `offers_text.json` and the field `currency` of `.choregos/fields.json`. An inline
JSON input is mounted under its artifact name plus `.json`. All arithmetic is `decimal.Decimal`, rounded
half-even to two places; the only float is the JSON number an amount is written as, after the arithmetic.

- **Per supplier**: `lines_total` (a null line total is counted in `lines_without_total`),
  `line_mismatches` (`quantity × unit_price` against `line_total`, more than 0.01 apart),
  `total_mismatch` (`stated_total` against `lines_total`, same tolerance, only when a line has a total),
  `total` (the stated total when present, else `lines_total`).
- **Output `comparison`**: the suppliers, `ranking` by `total` then name, `lowest_total`,
  `highest_total`, `spread`, `spread_ratio`.
- **Output `award_basis`**: `{totals, lowest_total, highest_total, spread, mismatches: [{supplier, kind,
  expected, written}]}`, the document a memo must quote in full. `kind` is `line_total` or `stated_total`.
- **Facts**: `suppliers_count`, `files_unassigned`, `currencies` (distinct among the suppliers' own and the
  request's `currency`; a supplier with none counts as one more), `mismatches`, `lowest_total`,
  `highest_total` (the last two are absent when there is no supplier).

## `tranibor-procurement tier`

```
tranibor-procurement tier --committee-from AMOUNT
```

Reads `.choregos/inputs/award_basis.json`. Output `approval_tier` is `committee` when `highest_total` is
at least the amount, else `standard`; output `highest_total`; fact `approval_tier_threshold`. A missing or
non-numeric `--committee-from` exits non-zero and writes nothing: no threshold never means the lighter tier.

## `tranibor-procurement supplier-check`

```
tranibor-procurement supplier-check --required KIND [KIND ...] --as-of DATE|today [--duplicates]
```

Reads `.choregos/inputs/supplier_file.json` (the shape of `src/tranibor_procurement/schemas/supplier_file.schema.json`:
`identity` and `documents`, `kind` an open string, dates ISO or null) and the fields `legal_name` and
`registration_number` of `.choregos/fields.json` (and `country`, for the duplicate search). The kinds to
require are arguments the workflow writes: there is no default. No bank detail is read, and a document's
`identifier` is not copied out.

- **Facts**: `documents_missing` (a required kind no document has), `documents_expired` (every document of a
  required kind expired before `--as-of`; one that expires on the day is valid, a null `expires_on` never
  expires), `identity_mismatch` (the legal name and the registration number of the file against the fields,
  compared after case, spaces and punctuation are dropped, so one digit is a mismatch), and with
  `--duplicates` `duplicates_found`.
- **Output `supplier_check`**: `{as_of, required, missing, expired, identity_mismatch, duplicates?}`, each finding listed.
- **Duplicates**: `purchasing.search_suppliers` through the relay (below), by name, registration number and country.
  A relay that fails exits 2 and writes nothing: it is never `duplicates_found: 0`.

## `tranibor-procurement renewals`

```
tranibor-procurement renewals --within-days N [--max 50]
```

Calls `purchasing.list_contracts` (`ending_before` today plus N days) through the relay. Output `contracts` =
`{expiring: [id], by_id: {id: summary}, left_out: [id]}`, ordered by end date (`end_date` of the summary)
then id; `--max` (1 to 100, the spawn ceiling of ADR 0057) children are listed, the rest are `left_out`.
Facts: `contracts_expiring`, `contracts_left_out`. No contract is `contracts_expiring: 0` and exit 0.

### The relay

`POST {TRANIBOR_API_URL}/runs/{TRANIBOR_RUN_ID}/tools/purchasing__{operation}` with `Bearer $TRANIBOR_RUN_TOKEN`
(standard library, 60 s timeout). The actor's `tools: [{connector: purchasing, tools: [...]}]` in the workflow
must name the operation, or the API answers 404. Pages are asked with `limit` and `cursor` and followed
through `next_cursor`.
