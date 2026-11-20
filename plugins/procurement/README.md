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
