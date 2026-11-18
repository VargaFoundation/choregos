# SPDX-License-Identifier: Apache-2.0
"""The audit log's hash chain (ADR 0182): walk it, report the first break, sign its checkpoints.

PostgreSQL links every entry as it is inserted (`choregos_audit_link`, migration `e2a4c6b8d0f3`): one
chain per organisation and one for the platform's entries, each entry carrying its place (`seq`), the
hash of the entry before it (`prev_hash`) and its own (`hash`). This module recomputes every hash from the
columns, **without the database's function**: the canonical form is specified in ADR 0182 and written
here a second time, so a verifier does not trust the code it checks.

What a walk sees, entry by entry and in the order of `seq`: an entry whose content no longer gives its
hash (altered), a place missing (deleted), a place taken twice (a fork), an entry that does not link to
the one before it (rewritten), an entry outside the chain (inserted with the trigger off), and, at the
end, a chain shorter than its head or than a checkpoint (deleted from the end). A rewrite of the whole
chain from some entry on, hashes recomputed, is seen only against a checkpoint kept where the rewriter
could not reach: one signed with a key the database does not hold, or one copied outside (`--anchor`).

The commands are `choregos-admin audit verify` and `choregos-admin audit checkpoint` (`admin_cli.py`).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import PUBLIC_KEY, ed25519_public_key
from .db.models import AuditChainHead, AuditCheckpoint, Organization

#: The first element of an entry's canonical form; a new form gets a new tag, never a new meaning.
VERSION = "choregos.audit/1"
#: The first element of a checkpoint's signed statement.
CHECKPOINT_VERSION = "choregos.audit.checkpoint/1"
#: What the first entry of a chain links to.
GENESIS = "0" * 64
#: The chain key of the platform's entries (`org_id IS NULL`); an organisation's is its id.
PLATFORM = ""
#: How many entries a walk reads per query.
PAGE = 5000


@dataclass(frozen=True)
class Link:
    """One entry as stored. `payload` is the stored `jsonb` as PostgreSQL renders it (`payload::text`),
    taken verbatim: parsing it back into Python and writing it again would not give the same text for
    every number."""

    id: str
    org_id: str | None
    seq: int | None
    ts: datetime
    actor_kind: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    payload: str
    prev_hash: str | None
    hash: str | None


def timestamp(moment: datetime) -> str:
    """UTC to the microsecond, `2026-11-17T23:00:00.000000Z`: what `to_char(ts AT TIME ZONE 'UTC',
    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')` gives."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(link: Link) -> str:
    """The entry's canonical form (ADR 0182): the JSON array PostgreSQL's `jsonb` renders for
    `[VERSION, org_id, seq, id, ts, actor_kind, actor_id, action, target_type, target_id, payload]`, that
    is ", " between the elements and strings escaped as `json.dumps(..., ensure_ascii=False)` escapes
    them."""
    head = [
        VERSION,
        link.org_id,
        link.seq,
        link.id,
        timestamp(link.ts),
        link.actor_kind,
        link.actor_id,
        link.action,
        link.target_type,
        link.target_id,
    ]
    return (
        "[" + ", ".join(json.dumps(value, ensure_ascii=False) for value in head) + ", " + link.payload + "]"
    )


def link_hash(prev_hash: str, link: Link) -> str:
    """`sha256(prev_hash || canonical(entry))`, lower-case hex."""
    return hashlib.sha256((prev_hash + canonical(link)).encode("utf-8")).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class Checkpoint:
    """A point of a chain someone vouched for: the genesis the migration wrote, a signed head, or a
    signed head copied outside the database (an anchor)."""

    chain: str
    seq: int
    hash: str
    kind: str
    created_at: datetime
    key_id: str | None = None
    signature: str | None = None
    #: Where it was read: `database`, or the anchor file it came from.
    source: str = "database"

    def statement(self) -> bytes:
        """What is signed: the chain (an organisation's id, `""` for the platform), the place, the hash and
        the moment, as compact JSON with sorted keys."""
        return json.dumps(
            {
                "v": CHECKPOINT_VERSION,
                "chain": self.chain,
                "seq": self.seq,
                "hash": self.hash,
                "at": timestamp(self.created_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def signature_holds(self) -> bool:
        """Whether `signature` is `key_id`'s over `statement()`."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if not self.key_id or not self.signature or not PUBLIC_KEY.fullmatch(self.key_id):
            return False
        try:
            public = Ed25519PublicKey.from_public_bytes(_unb64(self.key_id))
            public.verify(_unb64(self.signature), self.statement())
        except (InvalidSignature, ValueError):
            return False
        return True

    def as_anchor(self, label: str) -> dict[str, Any]:
        """The line `choregos-admin audit checkpoint` prints, to be kept outside the database."""
        return {
            "v": CHECKPOINT_VERSION,
            "org": label,
            "chain": self.chain,
            "seq": self.seq,
            "hash": self.hash,
            "at": timestamp(self.created_at),
            "key_id": self.key_id,
            "signature": self.signature,
        }


def sign(chain: str, seq: int, hash_: str, at: datetime, private_pem: str) -> Checkpoint:
    """A checkpoint of `chain` at `seq`, signed with an Ed25519 private key (PEM, PKCS#8), the key format
    of the outposts' door (`config.ed25519_public_key`). `ValueError` when the text is not such a key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_id = ed25519_public_key(private_pem)
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):  # `ed25519_public_key` already refused it
        raise ValueError("an Ed25519 key is expected")
    unsigned = Checkpoint(chain=chain, seq=seq, hash=hash_, kind="signed", created_at=at, key_id=key_id)
    signature = _b64(key.sign(unsigned.statement()))
    return Checkpoint(
        chain=chain, seq=seq, hash=hash_, kind="signed", created_at=at, key_id=key_id, signature=signature
    )


def read_anchors(lines: Iterable[str], source: str) -> list[Checkpoint]:
    """Checkpoints kept outside the database, one JSON object per line as `checkpoint` prints them.
    `ValueError` names the first line that is not one."""
    found: list[Checkpoint] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if item.get("v") != CHECKPOINT_VERSION:
                raise ValueError(f"not a {CHECKPOINT_VERSION} line")
            found.append(
                Checkpoint(
                    chain=str(item["chain"]),
                    seq=int(item["seq"]),
                    hash=str(item["hash"]),
                    kind="signed",
                    created_at=datetime.fromisoformat(str(item["at"]).replace("Z", "+00:00")),
                    key_id=item.get("key_id"),
                    signature=item.get("signature"),
                    source=source,
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"{source}:{number}: not a checkpoint ({error})") from None
    return found


@dataclass(frozen=True)
class Break:
    """Where the chain stops being evidence: the first place that fails, and why."""

    seq: int | None
    entry_id: str | None
    reason: str


@dataclass
class Report:
    """What a walk of one chain found."""

    chain: str
    label: str
    entries: int = 0
    last_seq: int = 0
    last_hash: str = GENESIS
    #: The genesis checkpoint: entries up to its place were chained by the migration, not as written.
    genesis: Checkpoint | None = None
    #: The signed checkpoints (database and anchors) the chain matched, the latest last.
    matched: list[Checkpoint] = field(default_factory=list)
    #: The first break; `None` when the chain is intact.
    first_break: Break | None = None
    #: What is wrong besides the chain itself: a checkpoint whose signature fails, or whose key is not
    #: trusted. Each one fails the verdict.
    problems: list[str] = field(default_factory=list)

    @property
    def intact(self) -> bool:
        return self.first_break is None and not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "org": self.label,
            "chain": self.chain,
            "intact": self.intact,
            "entries": self.entries,
            "last_seq": self.last_seq,
            "last_hash": self.last_hash,
            "genesis_seq": self.genesis.seq if self.genesis else None,
            "genesis_at": timestamp(self.genesis.created_at) if self.genesis else None,
            "signed_checkpoints": len(self.matched),
            "first_break": (
                {
                    "seq": self.first_break.seq,
                    "entry_id": self.first_break.entry_id,
                    "reason": self.first_break.reason,
                }
                if self.first_break
                else None
            ),
            "problems": list(self.problems),
        }


class ChainWalker:
    """Walks one chain, entry by entry in the order of `seq`, and stops at the first break.

    `checkpoints` are this chain's; a signed one whose signature fails, or whose key is not in `trusted`
    when `trusted` names keys, is reported as a problem and anchors nothing.
    """

    def __init__(
        self,
        chain: str,
        label: str,
        checkpoints: Iterable[Checkpoint] = (),
        trusted: Collection[str] = (),
    ) -> None:
        self.report = Report(chain=chain, label=label)
        self._expected = 1
        self._prev = GENESIS
        self._anchors: dict[int, list[Checkpoint]] = {}
        for checkpoint in sorted(checkpoints, key=lambda c: (c.seq, c.created_at)):
            if checkpoint.chain != chain:
                continue
            if checkpoint.kind == "genesis":
                self.report.genesis = checkpoint
            elif not checkpoint.signature_holds():
                self.report.problems.append(
                    f"the checkpoint at entry {checkpoint.seq} ({checkpoint.source}, "
                    f"{timestamp(checkpoint.created_at)}) carries a signature that does not verify"
                )
                continue
            elif trusted and checkpoint.key_id not in trusted:
                self.report.problems.append(
                    f"the checkpoint at entry {checkpoint.seq} ({checkpoint.source}) is signed by "
                    f"{checkpoint.key_id}, which is not a trusted key"
                )
                continue
            self._anchors.setdefault(checkpoint.seq, []).append(checkpoint)

    def _stop(self, seq: int | None, entry_id: str | None, reason: str) -> bool:
        self.report.first_break = Break(seq=seq, entry_id=entry_id, reason=reason)
        return False

    def feed(self, link: Link) -> bool:
        """One more entry; `False` once the chain is broken (the rest is not read)."""
        if self.report.first_break is not None:
            return False
        if link.seq is None:
            return self._stop(
                None, link.id, "an entry is outside the chain: it was inserted with the link trigger off"
            )
        if link.seq < self._expected:
            return self._stop(link.seq, link.id, f"entry {link.seq} appears twice: the chain forks")
        if link.seq > self._expected:
            missing = (
                f"entry {self._expected} is missing"
                if link.seq == self._expected + 1
                else f"entries {self._expected} to {link.seq - 1} are missing"
            )
            return self._stop(self._expected, link.id, f"{missing}: deleted after they were written")
        if link.prev_hash != self._prev:
            return self._stop(
                link.seq,
                link.id,
                f"entry {link.seq} does not link to entry {link.seq - 1}: one of the two was rewritten",
            )
        computed = link_hash(self._prev, link)
        if link.hash != computed:
            return self._stop(
                link.seq,
                link.id,
                f"entry {link.seq} was altered after it was written: it no longer gives its hash",
            )
        for checkpoint in self._anchors.get(link.seq, []):
            if checkpoint.hash != computed:
                return self._stop(
                    link.seq,
                    link.id,
                    f"entry {link.seq} is not the one the {checkpoint.kind} checkpoint of "
                    f"{timestamp(checkpoint.created_at)} ({checkpoint.source}) vouched for: the chain was "
                    "rewritten from there or before",
                )
            if checkpoint.kind == "signed":
                self.report.matched.append(checkpoint)
        self.report.entries += 1
        self.report.last_seq = link.seq
        self.report.last_hash = computed
        self._prev = computed
        self._expected += 1
        return True

    def finish(self, head: tuple[int, str] | None) -> Report:
        """The end of the chain against its head and against the checkpoints past it."""
        if self.report.first_break is not None:
            return self.report
        beyond = sorted(seq for seq in self._anchors if seq > self.report.last_seq)
        if beyond:
            self._stop(
                self.report.last_seq + 1,
                None,
                f"a checkpoint vouches for entry {beyond[-1]}, and the chain ends at entry "
                f"{self.report.last_seq}: entries were deleted from the end",
            )
        elif head is not None and head != (self.report.last_seq, self.report.last_hash):
            seq, hash_ = head
            self._stop(
                min(seq, self.report.last_seq) + 1,
                None,
                f"the chain's head is entry {seq} ({hash_[:12]}…), the chain ends at entry "
                f"{self.report.last_seq}: entries were deleted from the end, or the head was moved",
            )
        return self.report


async def chains(
    session: AsyncSession, orgs: Collection[str] = (), platform: bool = True
) -> list[tuple[str, str]]:
    """`(chain, label)` for the organisations named by slug (every one when none is named), then the
    platform's when `platform`. `LookupError` names a slug that is no organisation."""
    rows = (
        await session.execute(select(Organization.id, Organization.slug).order_by(Organization.slug))
    ).all()
    by_slug = {slug: org_id for org_id, slug in rows}
    unknown = sorted(set(orgs) - set(by_slug))
    if unknown:
        raise LookupError(f"no such organisation: {', '.join(unknown)}")
    named = [(by_slug[slug], slug) for slug in sorted(orgs)] if orgs else [(i, s) for i, s in rows]
    return named + ([(PLATFORM, "platform")] if platform else [])


#: An entry's columns as a walk reads them: the payload as PostgreSQL renders it.
_COLUMNS = (
    "id, org_id, seq, ts, actor_kind, actor_id, action, target_type, target_id, "
    "payload::text AS payload, prev_hash, hash"
)


def _where(chain: str) -> str:
    return "org_id IS NULL" if chain == PLATFORM else "org_id = :chain"


async def _links(session: AsyncSession, chain: str) -> AsyncIterator[Link]:
    """The chain's entries in the order of `(seq, id)`, a page at a time; entries outside the chain
    (`seq IS NULL`) first."""
    # Constants and `_where`'s two literals: nothing of the caller's goes into the text, `chain` is bound.
    select_links = f"SELECT {_COLUMNS} FROM audit_log WHERE {_where(chain)}"  # noqa: S608
    outside = await session.execute(
        text(f"{select_links} AND seq IS NULL ORDER BY ts, id LIMIT 1"), {"chain": chain}
    )
    for row in outside.mappings():
        yield Link(**row)
    after: tuple[int, str] = (0, "")
    while True:
        page = (
            await session.execute(
                text(
                    f"{select_links} AND seq IS NOT NULL AND (seq, id) > (:seq, :id) "
                    "ORDER BY seq, id LIMIT :page"
                ),
                {"chain": chain, "seq": after[0], "id": after[1], "page": PAGE},
            )
        ).mappings()
        rows = [Link(**row) for row in page]
        for link in rows:
            yield link
        if len(rows) < PAGE:
            return
        last = rows[-1]
        after = (int(last.seq or 0), last.id)


async def stored_checkpoints(session: AsyncSession, chain: str) -> list[Checkpoint]:
    owner = AuditCheckpoint.org_id.is_(None) if chain == PLATFORM else AuditCheckpoint.org_id == chain
    rows = (await session.execute(select(AuditCheckpoint).where(owner))).scalars()
    return [
        Checkpoint(
            chain=chain,
            seq=row.seq,
            hash=row.hash,
            kind=row.kind,
            created_at=row.created_at,
            key_id=row.key_id,
            signature=row.signature,
        )
        for row in rows
    ]


async def verify(
    session: AsyncSession,
    chain: str,
    label: str,
    *,
    trusted: Collection[str] = (),
    anchors: Iterable[Checkpoint] = (),
) -> Report:
    """Walk one chain (PostgreSQL), against its stored checkpoints, the `anchors` kept outside and its
    head. The session must see the chain: the platform's scope (`*`)."""
    checkpoints = [*await stored_checkpoints(session, chain), *(a for a in anchors if a.chain == chain)]
    walker = ChainWalker(chain, label, checkpoints, trusted)
    async for link in _links(session, chain):
        if not walker.feed(link):
            break
    head = await session.get(AuditChainHead, chain)
    return walker.finish((head.seq, head.hash) if head is not None else None)


async def checkpoint(
    session: AsyncSession,
    chain: str,
    label: str,
    private_pem: str,
    *,
    at: datetime,
    trusted: Collection[str] = (),
) -> tuple[Report, Checkpoint | None]:
    """Verify the chain, then sign its last entry and keep the checkpoint. Nothing is signed for a broken
    chain or an empty one. Only the signing key's own public half and `trusted` (the former keys of a
    rotation) are trusted: a stored checkpoint signed by any other key fails the verification, so that
    nothing is ever signed on top of a checkpoint someone else made."""
    key_id = ed25519_public_key(private_pem)
    report = await verify(session, chain, label, trusted={*trusted, key_id})
    if not report.intact or report.entries == 0:
        return report, None
    signed = sign(chain, report.last_seq, report.last_hash, at, private_pem)
    session.add(
        AuditCheckpoint(
            org_id=chain or None,
            seq=signed.seq,
            hash=signed.hash,
            kind="signed",
            created_at=signed.created_at,
            key_id=signed.key_id,
            signature=signed.signature,
        )
    )
    await session.flush()
    return report, signed
