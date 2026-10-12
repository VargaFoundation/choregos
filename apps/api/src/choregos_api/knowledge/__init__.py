# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases (ADR 0051): curated reference corpora, converted to markdown, read-only for agents.

- `formats`: what an upload may be, checked before anything parses it;
- `convert`: the child process that turns one document into markdown (MarkItDown);
- `conversion`: the background queue that runs it, never inside a request.
"""
