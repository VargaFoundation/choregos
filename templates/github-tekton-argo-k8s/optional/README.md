# Optional GitHub add-ons

These workflows are **not** part of Choregos's core: they add direct interactions with an agent
from GitHub, beside the platform. They are **disabled by default**; copy them into the project's
`.github/workflows/` if you want them.

- `claude-mention.yml` — answer `@claude` in an issue or a pull request.
- `claude-code-review.yml` — automatic review of a pull request.

Know what you give up by using them outside the platform: no budget per run, no gates, no
allowed paths, no cost on the work item.
