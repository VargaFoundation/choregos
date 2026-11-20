# SPDX-License-Identifier: Apache-2.0
"""An audit entry as a SIEM receives it (ADR 0196).

Every event carries what a receiver needs to verify the chain **without the platform**: the chain's
place (`seq`), the hash of the entry before it (`prev_hash`) and its own (`hash`), and, unless switched
off, `canonical`: the exact text `hash = sha256(prev_hash || canonical)` covers (ADR 0182 point 2). A
receiver that keeps the last `hash` of each chain can see a missing, reordered or altered entry; the
algorithm is `tranibor_api.audit_chain.link_hash`, six lines in any language.

The same event is written in three forms: a JSON object (`as_json`), RFC 5424 structured data
(`structured_data`) and CEF (`as_cef`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..audit_chain import Link, canonical, timestamp

#: The first field of every exported event; a new shape gets a new tag, never a new meaning.
SCHEMA = "choregos.audit.export/1"
#: The product's name and the version of the CEF header's `Device Version` field.
CEF_VENDOR, CEF_PRODUCT = "Choregos", "Choregos"


@dataclass(frozen=True)
class ExportEvent:
    """One entry of one chain, ready to ship. `seq`, `prev_hash` and `hash` are never absent: an entry
    outside the chain (`seq IS NULL`) is not exported."""

    link: Link
    seq: int
    prev_hash: str
    hash: str
    #: The organisation's slug, or `platform`.
    org: str
    source: str = "choregos"
    with_canonical: bool = True

    @property
    def chain(self) -> str:
        return self.link.org_id or ""

    @property
    def key(self) -> str:
        """`<chain>:<seq>`: unique, and the same for every delivery of the entry, so a receiver that can
        deduplicate (Elastic's `_id`) turns at-least-once into once."""
        return f"{self.chain or 'platform'}:{self.seq}"

    @property
    def moment(self) -> datetime:
        return self.link.ts

    @property
    def epoch(self) -> float:
        """Seconds since the epoch; a naive time (SQLite) is UTC, as `audit_chain.timestamp` takes it."""
        return _epoch_ms(self.link.ts) / 1000

    def as_json(self) -> dict[str, Any]:
        link = self.link
        body: dict[str, Any] = {
            "schema": SCHEMA,
            "source": self.source,
            "org": self.org,
            "org_id": link.org_id,
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "id": link.id,
            "ts": timestamp(link.ts),
            "actor_kind": link.actor_kind,
            "actor_id": link.actor_id,
            "action": link.action,
            "target_type": link.target_type,
            "target_id": link.target_id,
            "payload": json.loads(link.payload),
        }
        if self.with_canonical:
            body["canonical"] = canonical(link)
        return body

    def message(self) -> str:
        """The event as one line of compact JSON."""
        return json.dumps(self.as_json(), ensure_ascii=False, separators=(",", ":"))

    # RFC 5424 ---------------------------------------------------------------------------------------

    def structured_data(self, sd_id: str) -> str:
        """`[<sd_id> chain="…" seq="…" prev_hash="…" hash="…" action="…" actor="…"]`: what a collector can
        index without parsing the message (RFC 5424 §6.3, values escaped as §6.3.3 says)."""
        params = {
            "org": self.org,
            "seq": str(self.seq),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "action": self.link.action,
            "actor_kind": self.link.actor_kind,
        }
        if self.link.actor_id:
            params["actor_id"] = self.link.actor_id
        inside = " ".join(f'{name}="{_sd_value(value)}"' for name, value in params.items())
        return f"[{sd_id} {inside}]"

    # CEF --------------------------------------------------------------------------------------------

    def as_cef(self, version: str) -> str:
        """`CEF:0|Choregos|Choregos|<version>|<action>|<action>|3|<extension>`: the header's fields escape
        `\\` and `|`, the extension's values `\\`, `=` and line breaks. Severity 3 is "low": an audit entry
        is a record, not an alarm. The chain's fields travel as custom strings and a custom number, labelled,
        and the full event in `msg`."""
        link = self.link
        header = "|".join(
            _cef_header(part)
            for part in (
                CEF_VENDOR,
                CEF_PRODUCT,
                version,
                link.action,
                link.action,
                "3",
            )
        )
        extension: list[tuple[str, str]] = [
            ("rt", str(_epoch_ms(link.ts))),
            ("externalId", link.id),
            ("act", link.action),
            ("cat", link.actor_kind),
            ("cs1Label", "org"),
            ("cs1", self.org),
            ("cn1Label", "seq"),
            ("cn1", str(self.seq)),
            ("cs2Label", "prevHash"),
            ("cs2", self.prev_hash),
            ("cs3Label", "hash"),
            ("cs3", self.hash),
        ]
        if link.actor_id:
            extension.append(("suser", link.actor_id))
        if link.target_type:
            extension.append(("cs4Label", "targetType"))
            extension.append(("cs4", link.target_type))
        if link.target_id:
            extension.append(("cs5Label", "targetId"))
            extension.append(("cs5", link.target_id))
        extension.append(("msg", self.message()))
        return f"CEF:0|{header}|" + " ".join(f"{name}={_cef_value(value)}" for name, value in extension)


def _epoch_ms(moment: datetime) -> int:
    """Milliseconds since the epoch; a naive time (SQLite) is UTC, as `audit_chain.timestamp` takes it."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return int(aware.timestamp() * 1000)


def _sd_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _cef_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _cef_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("=", "\\=")
    return escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
