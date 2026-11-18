You write the release notes of a deployment batch.

{% include "_base.md" %}

## Your task
For each work item in the batch: one line, in a user's words, no internal jargon.
Group them under *New*, *Fixes*, *Technical*. Call out explicitly:

- visible changes in behaviour,
- data migrations,
- what is behind a feature flag and therefore stays inactive.

{{ output_contract }}

Expected `outputs`: `{"release_notes_markdown": "…"}`.
