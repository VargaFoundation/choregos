# SPDX-License-Identifier: Apache-2.0
"""Run the migrations: the core's AND the ones extensions declare.

    python -m tranibor_api.migrate              # upgrade heads
    python -m tranibor_api.migrate downgrade -1

This is the command of the chart's migration Job. It replaces `alembic -c alembic.ini upgrade
head`, which had two defects:

1. **It only worked from `/app/apps/api`**: `script_location = migrations` was relative, and the
   migrations were not in the `choregos-api` wheel (59 files, no `migrations/`). Outside the image,
   nobody could migrate a database with the published core. They now live INSIDE the package
   (`tranibor_api/migrations`), and this command finds them wherever it is launched from.
2. **An extension could not bring a schema.** It now declares a versions location in the
   `choregos.migrations` entry-point group; its revisions form an Alembic BRANCH (`branch_labels`,
   `depends_on` a core revision) in the same `alembic_version` table, and `upgrade heads` runs the
   core, then the branch. An extension creates ITS tables; it never alters the core's: two owners
   for one schema, and the next core migration is what breaks.

A declared location that does not exist stops the command: a silently missing extension schema
would give a platform that starts and fails at the first request touching its tables.

A branch can also leave with its package. The ontology's did (ADR 0181, O4-1): a database that applied
it still names one of its revisions in `alembic_version`, which Alembic can no longer resolve, and it
would refuse to run at all. Before anything else, `env.py` forgets those rows
(`forget_retired_revisions`), and a core migration drops the tables they had created.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Sequence
from importlib import metadata

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import Revision

#: The entry-point group where an extension declares its migrations. The value it points to is a
#: path (`str` or `pathlib.Path`) to a directory of Alembic revisions.
MIGRATIONS_GROUP = "choregos.migrations"

MIGRATIONS = pathlib.Path(__file__).resolve().parent / "migrations"


#: The revisions of branches whose package was removed, each with the core revision it depended on
#: (`depends_on` of the branch's first revision, or of the revision itself when it names one). The
#: ontology's branch, `onto0001` to `onto0003`, left in 0.28 (ADR 0181, O4-1).
RETIRED_REVISIONS: dict[str, str] = {
    "onto0001": "e5a7c9b1d3f5",
    "onto0002": "e5a7c9b1d3f5",
    "onto0003": "f8a0b2c4d6e9",
}
VERSION_TABLE = "alembic_version"


def _names(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def _covered(script: ScriptDirectory, revisions: set[str]) -> set[str]:
    """The revisions the rows `revisions` stand for: themselves, their ancestors and what they depend on."""
    seen: set[str] = set()
    todo = list(revisions)
    while todo:
        revision = script.get_revision(todo.pop())
        if revision is None or revision.revision in seen:
            continue
        seen.add(revision.revision)
        for name in (*_names(revision.down_revision), *_names(revision.dependencies)):
            todo += [r.revision for r in script.revision_map.get_revisions(name) if isinstance(r, Revision)]
    return seen


def forget_retired_revisions(connection: sa.Connection, script: ScriptDirectory) -> list[str]:
    """Remove the rows of retired branches from `alembic_version`, so that Alembic can read it again.

    A row also stood for the core revision its branch depended on (Alembic keeps only the most dependent
    revision): when no row left covers that revision, it is written in its place, so the core is not
    thought to be further back than it is. Run in its own transaction, before Alembic reads the table.
    Returns the rows forgotten; an installation that never applied a retired branch is not touched.
    """
    if not sa.inspect(connection).has_table(VERSION_TABLE):
        return []
    rows = {str(r[0]) for r in connection.execute(sa.text("SELECT version_num FROM alembic_version"))}
    retired = sorted(rows & RETIRED_REVISIONS.keys())
    if not retired:
        return []
    kept = rows - set(retired)
    covered = _covered(script, kept)
    stood_on = {RETIRED_REVISIONS[r] for r in retired} - covered
    # Of the core revisions to write back, only those no other one covers: one row per branch.
    stood_on = {r for r in stood_on if not any(r in _covered(script, {o}) for o in stood_on - {r})}
    for revision in retired:
        connection.execute(sa.text("DELETE FROM alembic_version WHERE version_num = :r"), {"r": revision})
    for revision in sorted(stood_on):
        connection.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision})
    return retired


def extension_locations(group: str = MIGRATIONS_GROUP) -> list[pathlib.Path]:
    locations: list[pathlib.Path] = []
    for point in metadata.entry_points(group=group):
        value = point.load()
        if not isinstance(value, str | os.PathLike):
            raise TypeError(
                f"an extension's migrations '{point.name}' ({point.value}): a path is expected, "
                f"not {type(value).__name__}"
            )
        path = pathlib.Path(value).resolve()
        if not path.is_dir():
            raise RuntimeError(
                f"an extension's migrations '{point.name}': {path} does not exist. They are "
                "declared, so they must be run; the database would lack their tables."
            )
        locations.append(path)
    return locations


def configuration(*, with_extensions: bool = True) -> Config:
    """The Alembic configuration, built here rather than read from a file relative to the cwd."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("path_separator", "os")
    locations = [MIGRATIONS / "versions"]
    if with_extensions:
        locations += extension_locations()
    config.set_main_option("version_locations", os.pathsep.join(str(loc) for loc in locations))
    return config


def main(arguments: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    action = arguments[0] if arguments else "upgrade"
    target = arguments[1] if len(arguments) > 1 else ("heads" if action == "upgrade" else "-1")
    config = configuration()
    if action == "upgrade":
        command.upgrade(config, target)
    elif action == "downgrade":
        command.downgrade(config, target)
    elif action == "heads":
        command.heads(config, verbose=True)
    elif action == "current":
        command.current(config, verbose=True)
    else:
        raise SystemExit(f"unknown action: {action!r} (upgrade, downgrade, heads, current)")


if __name__ == "__main__":
    main()
