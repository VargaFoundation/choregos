# SPDX-License-Identifier: Apache-2.0
"""What a run's `tool` lines of the cost ledger say, for the API and the orchestrator alike (S57-03).

A call under a run's tool-call cap takes its place before it leaves: a provisional `tool` line whose
provider is `IN_FLIGHT`, replaced by the call's own line when it answers. Until then the line holds a
place under the cap and nothing else: it is not a call that was made. Every reader that takes a
`tool` line for a call (the `tool_called` gate, the run's access record, the cost reports) leaves
it out.

A provisional line outlives its call only if the API process died during the call. The next call of
the same run deletes the ones older than `IN_FLIGHT_STALE_AFTER`, under the run's lock.
"""

from __future__ import annotations

from datetime import timedelta

#: The provider of a provisional line: the call holds its place under the cap and has not answered.
IN_FLIGHT = "in_flight"

#: Beyond this, a provisional line is a call whose request died: far above the longest wait of a
#: call (a connector's 30 s, the governed write's bounded wait, the tool bridge's 60 s).
IN_FLIGHT_STALE_AFTER = timedelta(minutes=10)

__all__ = ["IN_FLIGHT", "IN_FLIGHT_STALE_AFTER"]
