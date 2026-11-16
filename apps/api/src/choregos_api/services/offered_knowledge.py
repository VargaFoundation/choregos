# SPDX-License-Identifier: Apache-2.0
"""The knowledge an assistant offers (S63-06, ADR 0138 points 1-3).

A seed is a directory of the plugin (`knowledge.yaml` and a markdown `corpus/`). Installing the assistant
copies each seed into an organisation base `<installation>-<name>`, linked to the installation and owned
by its owner groups; each document is marked `origin: seed@<plugin version>` and keeps the digest of the
seed file it came from (`seed_digest`). An owner who edits or adds a document makes it `local`.

Upgrading the plugin compares, per document, the seed the copy came from (`seed_digest`), the new seed and
the local copy (`origin`):

- the new seed equals the old one: nothing;
- unchanged locally: `follow` updates it, `propose_updates` makes a proposal, `frozen` does nothing;
- changed on both sides: always a proposal, with its diff;
- new upstream: added (`follow`), proposed (`propose_updates`), nothing (`frozen`);
- removed upstream: a proposal to remove (never silently deleted), except `frozen` on a copy nobody changed.

The owners decide a proposal: accept (the seed's text), merge (their own text) or refuse.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any

import structlog
import yaml
from choregos_core import utcnow
from choregos_core.paths import files_under, inside
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..audit import record
from ..db.models import (
    AssistantInstallation,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeSeedTombstone,
    KnowledgeSource,
    KnowledgeUpdateProposal,
    Membership,
    OrgPlugin,
    User,
)
from ..db.session import after_commit
from ..errors import conflict, unprocessable
from ..knowledge.conversion import title_of
from ..rbac import Principal
from ..schemas.assistant_knowledge import UpdateDecision

log = structlog.get_logger("choregos.offered_knowledge")
PROPOSED, ACCEPTED, MERGED, REFUSED = "proposed", "accepted", "merged", "refused"
LOCAL = "local"
_MARKDOWN = (".md", ".markdown")


def seed_origin(version: str) -> str:
    return f"seed@{version}"


def base_slug(installation_slug: str, name: str) -> str:
    return f"{installation_slug}-{name}".replace("_", "-").lower()


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in document.get("knowledge") or [] if isinstance(e, dict) and e.get("seed")]


def _read_seed(root: Path, seed: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    """The seed's `knowledge.yaml` and its markdown files, by path under `corpus/`."""
    directory = inside(root, seed)
    manifest_path = directory / "knowledge.yaml"
    manifest = (
        (yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}) if manifest_path.is_file() else {}
    )
    corpus = directory / "corpus"
    found: dict[str, bytes] = {}
    if corpus.is_dir():
        for name, path in sorted(files_under(corpus).items()):
            if name.lower().endswith(_MARKDOWN):
                found[name] = path.read_bytes()
    return manifest if isinstance(manifest, dict) else {}, found


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _fill(document: KnowledgeDocument, path: str, data: bytes) -> None:
    """The document holds `data` (markdown), converted and ready."""
    text = _text(data)
    document.filename = path
    document.title = title_of(text, None, path)
    document.mime = "text/markdown"
    document.size_bytes = len(data)
    document.digest = _digest(data)
    document.content = data
    document.markdown = text
    document.status, document.error, document.warnings = "ready", None, []
    document.converted_at = utcnow()


def _reindex(session: AsyncSession, org: str, document_id: str) -> None:
    async def run() -> None:
        from ..knowledge.index import index_document

        await index_document(org, document_id)

    after_commit(session, run)


async def _org_slug(session: AsyncSession, org_id: str) -> str:
    from ..db.models import Organization

    return (await session.execute(select(Organization.slug).where(Organization.id == org_id))).scalar_one()


async def _documents(session: AsyncSession, base: KnowledgeBase) -> dict[str, KnowledgeDocument]:
    found = await session.execute(
        select(KnowledgeDocument)
        .options(undefer(KnowledgeDocument.content), undefer(KnowledgeDocument.markdown))
        .where(KnowledgeDocument.knowledge_base_id == base.id)
    )
    return {d.filename: d for d in found.scalars()}


async def _add(
    session: AsyncSession,
    org: str,
    base: KnowledgeBase,
    path: str,
    data: bytes,
    origin: str,
    seed_digest: str | None,
    who: str | None,
) -> KnowledgeDocument:
    document = KnowledgeDocument(
        org_id=base.org_id,
        knowledge_base_id=base.id,
        source="upload",
        origin=origin,
        seed_digest=seed_digest,
        created_by=who,
    )
    _fill(document, path, data)
    session.add(document)
    await session.flush()
    _reindex(session, org, document.id)
    return document


def _replace(
    session: AsyncSession,
    org: str,
    document: KnowledgeDocument,
    data: bytes,
    origin: str,
    seed_digest: str | None,
) -> None:
    _fill(document, document.filename, data)
    document.origin, document.seed_digest = origin, seed_digest
    _reindex(session, org, document.id)


# -- documents an owner deleted ---------------------------------------------------------------------------


async def _tombstones(session: AsyncSession, base: KnowledgeBase) -> set[str]:
    found = await session.execute(
        select(KnowledgeSeedTombstone.path).where(KnowledgeSeedTombstone.knowledge_base_id == base.id)
    )
    return set(found.scalars())


async def remember_deleted(
    session: AsyncSession, base: KnowledgeBase, document: KnowledgeDocument, who: str | None
) -> None:
    """An owner deletes a document of an installation's base that came from a seed: remember it, so that an
    upgrade does not add it back (ADR 0138 point 3). A document the owners wrote is not remembered."""
    if base.installation_id is None or document.seed_digest is None:
        return
    if document.filename in await _tombstones(session, base):
        return
    session.add(
        KnowledgeSeedTombstone(
            org_id=base.org_id, knowledge_base_id=base.id, path=document.filename, deleted_by=who
        )
    )


# -- the seed's own sources --------------------------------------------------------------------------------

#: The kinds a seed's `sources` may name (the ones a base's source routes know).
SOURCE_KINDS = ("sharepoint", "confluence", "web", "git")


async def _attach_sources(
    session: AsyncSession, base: KnowledgeBase, seed_manifest: dict[str, Any], who: str | None
) -> list[str]:
    """Attaches the `sources` of the seed's `knowledge.yaml` to the base, by name. A source is attached
    `manual` and never synced here: opening its hosts in the egress allowlist and syncing stay the owners'
    gestures (the source routes), so installing never reaches out."""
    known = set(
        (
            await session.execute(
                select(KnowledgeSource.name).where(KnowledgeSource.knowledge_base_id == base.id)
            )
        ).scalars()
    )
    attached: list[str] = []
    for item in seed_manifest.get("sources") or []:
        if not isinstance(item, dict) or item.get("kind") not in SOURCE_KINDS or not item.get("name"):
            continue
        name = str(item["name"])[:200]
        if name in known:
            continue
        config = item.get("config")
        session.add(
            KnowledgeSource(
                org_id=base.org_id,
                knowledge_base_id=base.id,
                kind=str(item["kind"]),
                name=name,
                connector=str(item["connector"]) if item.get("connector") else None,
                config=dict(config) if isinstance(config, dict) else {},
                interval_minutes=int(item.get("interval_minutes") or 60),
                refresh_mode="manual",
                next_sync_at=None,
                status="idle",
                created_by=who,
            )
        )
        attached.append(name)
    if attached:
        await session.flush()
    return attached


# -- install ---------------------------------------------------------------------------------------------


async def _create_base(
    session: AsyncSession,
    row: AssistantInstallation,
    entry: dict[str, Any],
    seed_manifest: dict[str, Any],
    who: str | None,
) -> KnowledgeBase:
    slug = base_slug(row.slug, str(entry["name"]))
    taken = (
        await session.execute(
            select(KnowledgeBase.id).where(KnowledgeBase.org_id == row.org_id, KnowledgeBase.slug == slug)
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise conflict(f"a knowledge base `{slug}` already exists: the assistant's seed cannot be copied")
    base = KnowledgeBase(
        org_id=row.org_id,
        slug=slug,
        name=str(seed_manifest.get("name") or entry["name"])[:200],
        description=seed_manifest.get("description"),
        # Read by the owners (through their grants) and served to the installation's conversations.
        scope="groups",
        groups=list(row.owner_groups),
        restricted=True,
        serving_mode=str(entry.get("serving_mode") or seed_manifest.get("serving_mode") or "files"),
        status="active",
        created_by=who,
        installation_id=row.id,
    )
    session.add(base)
    await session.flush()
    for group in row.owner_groups:
        session.add(
            KnowledgeGrant(
                org_id=row.org_id,
                knowledge_base_id=base.id,
                subject_type="group",
                subject=group,
                role="owner",
                created_by=who,
            )
        )
    await session.flush()
    return base


async def copy_seeds(
    session: AsyncSession,
    row: AssistantInstallation,
    document: dict[str, Any],
    plugin_row: OrgPlugin,
    root: Path,
    principal: Principal,
) -> list[str]:
    """Installing: a base per seed, each document imported as `seed@<version>`. Returns the base slugs."""
    org = await _org_slug(session, row.org_id)
    slugs: list[str] = []
    for entry in _entries(document):
        seed_manifest, files = _read_seed(root, str(entry["seed"]))
        base = await _create_base(session, row, entry, seed_manifest, principal.email)
        for path, data in files.items():
            await _add(
                session,
                org,
                base,
                path,
                data,
                seed_origin(plugin_row.version),
                _digest(data),
                principal.email,
            )
        await _attach_sources(session, base, seed_manifest, principal.email)
        await record(session, principal, "assistant.knowledge.seed", org_id=row.org_id,
                     target_type="knowledge_base", target_id=base.id, installation=row.slug,
                     documents=len(files), version=plugin_row.version)  # fmt: skip
        slugs.append(base.slug)
    return slugs


# -- upgrade ---------------------------------------------------------------------------------------------


def _diff(old: str, new: str, path: str, version: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"local/{path}",
            tofile=f"{seed_origin(version)}/{path}",
        )
    )


async def _propose(
    session: AsyncSession,
    row: AssistantInstallation,
    base: KnowledgeBase,
    path: str,
    kind: str,
    diff: str,
    content: bytes | None,
    version: str,
) -> str | None:
    """A proposal for the document, unless one is already open for it (it is refreshed) or the owners already
    refused this very change. Returns `opened` for a new proposal, `proposed` for one refreshed, `None` for
    none."""
    digest = _digest(content) if content is not None else None
    same = (
        (
            await session.execute(
                select(KnowledgeUpdateProposal).where(
                    KnowledgeUpdateProposal.installation_id == row.id,
                    KnowledgeUpdateProposal.knowledge_base_id == base.id,
                    KnowledgeUpdateProposal.path == path,
                )
            )
        )
        .scalars()
        .all()
    )
    if any(p.status == REFUSED and p.kind == kind and p.seed_digest == digest for p in same):
        return None
    open_one = next((p for p in same if p.status == PROPOSED), None)
    outcome = "proposed"
    if open_one is None:
        outcome = "opened"
        open_one = KnowledgeUpdateProposal(
            org_id=row.org_id, installation_id=row.id, knowledge_base_id=base.id, path=path, status=PROPOSED
        )
        session.add(open_one)
    open_one.kind, open_one.diff, open_one.seed_digest = kind, diff, digest
    open_one.proposed_content = _text(content) if content is not None else None
    open_one.plugin_version = version
    await session.flush()
    return outcome


async def _reconcile(
    session: AsyncSession,
    org: str,
    row: AssistantInstallation,
    base: KnowledgeBase,
    seed: dict[str, bytes],
    entry: dict[str, Any],
    version: str,
) -> list[tuple[str, str]]:
    """The three-way comparison of one base: what was `updated`, `added` or `proposed`, as (what, where)."""
    upstream = str(entry.get("upstream") or "propose_updates")
    local = await _documents(session, base)
    tombstones = await _tombstones(session, base)
    out: list[tuple[str, str]] = []
    for path, data in sorted(seed.items()):
        new = _digest(data)
        copy = local.get(path)
        where = f"{base.slug}/{path}"
        if copy is None:
            if path in tombstones:
                continue  # an owner deleted this document: it is not brought back, nor proposed again
            if upstream == "follow":
                await _add(session, org, base, path, data, seed_origin(version), new, None)
                out.append(("added", where))
            else:
                kind = (
                    await _propose(session, row, base, path, "add", "", data, version)
                    if (upstream == "propose_updates")
                    else None
                )
                if kind:
                    out.append((kind, where))
            continue
        if copy.seed_digest == new:
            continue  # the seed did not change under this copy
        untouched = copy.origin != LOCAL
        if untouched and upstream == "frozen":
            continue
        if untouched and upstream == "follow":
            _replace(session, org, copy, data, seed_origin(version), new)
            out.append(("updated", where))
            continue
        diff = _diff(_text(copy.content or b""), _text(data), path, version)
        if kind := await _propose(session, row, base, path, "update", diff, data, version):
            out.append((kind, where))
    for path, copy in sorted(local.items()):
        if path in seed or copy.seed_digest is None:
            continue  # still shipped, or written by the owners
        if copy.origin != LOCAL and upstream == "frozen":
            continue
        diff = _diff(_text(copy.content or b""), "", path, version)
        if kind := await _propose(session, row, base, path, "remove", diff, None, version):
            out.append((kind, f"{base.slug}/{path}"))
    return out


async def upgrade_installation(
    session: AsyncSession,
    row: AssistantInstallation,
    document: dict[str, Any],
    plugin_row: OrgPlugin,
    root: Path,
    principal: Principal,
) -> dict[str, list[str]]:
    """The three-way comparison for every seed of the installation's assistant, as the plugin is now."""
    version = plugin_row.version
    org = await _org_slug(session, row.org_id)
    done: dict[str, list[str]] = {"updated": [], "added": [], "proposed": [], "opened": []}
    for entry in _entries(document):
        seed_manifest, seed = _read_seed(root, str(entry["seed"]))
        slug = base_slug(row.slug, str(entry["name"]))
        base = (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.org_id == row.org_id,
                    KnowledgeBase.slug == slug,
                    KnowledgeBase.installation_id == row.id,
                )
            )
        ).scalar_one_or_none()
        if base is None:  # a seed this version brings: copied as at install
            base = await _create_base(session, row, entry, seed_manifest, principal.email)
            for path, data in seed.items():
                await _add(session, org, base, path, data, seed_origin(version), _digest(data), None)
            await _attach_sources(session, base, seed_manifest, principal.email)
            done["added"].extend(f"{slug}/{p}" for p in seed)
            continue
        for kind, where in await _reconcile(session, org, row, base, seed, entry, version):
            done[kind].append(where)
            if kind == "opened":  # `proposed` lists every open proposal touched, `opened` the new ones
                done["proposed"].append(where)
    if any(done.values()):
        await record(session, principal, "assistant.knowledge.upgrade", org_id=row.org_id,
                     target_type="assistant", target_id=row.slug, version=version, **done)  # fmt: skip
    return done


async def upgrade_plugin(
    session: AsyncSession, org_id: str, plugin_row: OrgPlugin, principal: Principal
) -> dict[str, dict[str, list[str]]]:
    """Every installation of an assistant of `plugin_row`, after the plugin moved to its new version."""
    from . import plugin_files

    found = await session.execute(
        select(AssistantInstallation).where(
            AssistantInstallation.org_id == org_id, AssistantInstallation.plugin == plugin_row.name
        )
    )
    outcome: dict[str, dict[str, list[str]]] = {}
    for row in found.scalars():
        assistant = await plugin_files.plugin_assistant(session, org_id, row.plugin, row.assistant_name)
        if assistant is None:
            continue
        root = await plugin_files.plugin_root(session, plugin_row)
        outcome[row.slug] = await upgrade_installation(
            session, row, assistant[1], plugin_row, root, principal
        )
        if outcome[row.slug]["opened"]:  # told once: a proposal already open is not announced again
            await notify_owners(session, row, plugin_row.version, outcome[row.slug]["opened"])
    return outcome


async def notify_owners(
    session: AsyncSession, row: AssistantInstallation, version: str, proposed: list[str]
) -> int:
    """Tells the owners, through the installation's `email` notifier (the one of its task deliveries), that an
    upgrade left proposals waiting. Nothing configured, nothing sent: the proposals stay listed in the owners'
    route. Returns the number of owners told; a failing channel never fails the upgrade."""
    return await tell_owners(
        session,
        row,
        f"{row.slug}: {len(proposed)} knowledge update(s) to decide",
        f"Version {version} of the plugin proposes changes to the knowledge of "
        f"`{row.slug}`:\n" + "\n".join(f"- {p}" for p in proposed),
        {"installation": row.slug, "version": version},
    )


async def tell_owners(
    session: AsyncSession, row: AssistantInstallation, title: str, body: str, context: dict[str, Any]
) -> int:
    """One message to every owner of `row` (members of an owner group) through its `email` notifier, and
    one card to its `teams` channel. Nothing configured, nothing sent. Returns the number of owners told by
    e-mail plus one when the Teams card went; a failing channel never fails the upgrade."""
    from choregos_core.domain import Message

    from .egress import egress_scope_for_org
    from .task_delivery import notifier_in

    told = 0
    teams = None
    try:
        teams = await notifier_in(session, row, "teams")
    except Exception as failure:  # a sealed URL that cannot be read must not undo the upgrade
        log.warning("assistant.knowledge.notify_failed", installation=row.slug, error=str(failure)[:200])
    if teams is not None:
        try:
            async with egress_scope_for_org(session, row.org_id, "api", "assistant.owner_notice"):
                await teams.send("", Message(title=title, body=body, context=context))
            told += 1
        except Exception as failure:
            log.warning("assistant.knowledge.notify_failed", installation=row.slug, error=str(failure)[:200])
    notifier = await notifier_in(session, row, "email")
    if notifier is None or not row.owner_groups:
        return told
    found = await session.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == row.org_id, Membership.project_id.is_(None))
    )
    owners = sorted({u.email for u in found.scalars() if set(u.groups or []) & set(row.owner_groups)})
    for email in owners:
        try:
            await notifier.send(email, Message(title=title, body=body, context=context))
            told += 1
        except Exception as failure:  # a channel that fails must not undo the upgrade
            log.warning("assistant.notify_failed", installation=row.slug, error=str(failure)[:200])
    return told


# -- the owners decide -----------------------------------------------------------------------------------


async def proposals(
    session: AsyncSession, row: AssistantInstallation, status: str | None = None
) -> list[tuple[KnowledgeUpdateProposal, str]]:
    query = (
        select(KnowledgeUpdateProposal, KnowledgeBase.slug)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeUpdateProposal.knowledge_base_id)
        .where(KnowledgeUpdateProposal.installation_id == row.id)
        .order_by(KnowledgeUpdateProposal.created_at.desc(), KnowledgeUpdateProposal.path)
    )
    if status is not None:
        query = query.where(KnowledgeUpdateProposal.status == status)
    return [(p, s) for p, s in (await session.execute(query)).all()]


async def decide(
    session: AsyncSession,
    row: AssistantInstallation,
    proposal: KnowledgeUpdateProposal,
    body: UpdateDecision,
    principal: Principal,
) -> KnowledgeUpdateProposal:
    if proposal.status != PROPOSED:
        raise conflict(f"the proposal is already {proposal.status}")
    if body.decision == "merge" and (body.content is None or proposal.kind == "remove"):
        raise unprocessable(
            "a merge carries the text to keep (`content`); a removal is accepted or refused, not merged"
        )
    base = await session.get(KnowledgeBase, proposal.knowledge_base_id)
    assert base is not None
    org = await _org_slug(session, row.org_id)
    copy = (await _documents(session, base)).get(proposal.path)
    origin = seed_origin(proposal.plugin_version)
    if body.decision == "refuse":
        proposal.status = REFUSED
    elif proposal.kind == "remove":
        if copy is not None:
            await session.delete(copy)
        proposal.status = ACCEPTED
    else:
        merged = body.decision == "merge"
        data = ((body.content if merged else proposal.proposed_content) or "").encode("utf-8")
        # A merge is the owners' own text: local, but relative to the seed they merged.
        new_origin = LOCAL if merged else origin
        if copy is None:
            await _add(
                session, org, base, proposal.path, data, new_origin, proposal.seed_digest, principal.email
            )
        else:
            _replace(session, org, copy, data, new_origin, proposal.seed_digest)
        proposal.status = MERGED if merged else ACCEPTED
    proposal.decided_by, proposal.decided_at = principal.email, utcnow()
    await session.flush()
    await record(session, principal, "assistant.knowledge.decide", org_id=row.org_id,
                 target_type="knowledge_update", target_id=proposal.id, installation=row.slug,
                 decision=body.decision, path=proposal.path, kind=proposal.kind)  # fmt: skip
    return proposal


__all__ = [
    "copy_seeds",
    "decide",
    "notify_owners",
    "proposals",
    "remember_deleted",
    "tell_owners",
    "upgrade_plugin",
]
