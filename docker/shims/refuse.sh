#!/usr/bin/env bash
# Refusal shim: an agent has no reason to call these tools from its workspace.
# The refusal is explicit and traced — it is defence in depth, not the guarantee
# (the guarantee is the NetworkPolicy, the absence of a credential and the diff check).
# The shim holds no credential and posts nothing (ADR 0115): its first line below is
# what the runner reads in the call's output and journals as `shim.refused`
# (`choregos_runner.refusal_shims`, #1346). Keep its wording, or change both.
tool=$(basename "$0")
message="choregos: \`$tool\` is refused in an agent workspace."
printf '%s\n' "$message" >&2
printf 'If this operation is needed for the ticket, ask a human for it with `ask_human`,\n' >&2
printf "or report the need with \`report_finding\`. Arguments received: %s\n" "$*" >&2
exit 127
