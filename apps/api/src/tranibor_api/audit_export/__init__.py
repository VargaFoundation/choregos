# SPDX-License-Identifier: Apache-2.0
"""The audit log leaves the database: the SIEM export (S80-05b, ADR 0207).

Three parts, each in its own module:

- `events`: an audit entry as the receiver gets it (`seq`, `prev_hash`, `hash`, and the canonical text the
  hash covers) in JSON, in RFC 5424 structured data and in CEF;
- `sinks`: where it goes (an HTTPS JSON endpoint, syslog over TLS, a scriptable fake);
- `exporter`: the loop that reads each chain after a durable cursor, ships a batch, and moves the cursor.
"""
