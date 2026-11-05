# SPDX-License-Identifier: Apache-2.0
"""Run the migrations: the core's AND the ones extensions declare.

    python -m choregos_api.migrate              # upgrade heads
    python -m choregos_api.migrate downgrade -1

This is the command of the chart's migration Job. It replaces `alembic -c alembic.ini upgrade
head`, which had two defects:

1. **It only worked from `/app/apps/api`**: `script_location = migrations` was relative, and the
   migrations were not in the `choregos-api` wheel (59 files, no `migrations/`). Outside the image,
   nobody could migrate a database with the published core. They now live INSIDE the package
   (`choregos_api/migrations`), and this command finds them wherever it is launched from.
2. **An extension could not bring a schema.** It now declares a versions location in the
   `choregos.migrations` entry-point group; its revisions form an Alembic BRANCH (`branch_labels`,
   `depends_on` a core revision) in the same `alembic_version` table, and `upgrade heads` runs the
   core, then the branch. An extension creates ITS tables; it never alters the core's: two owners
   for one schema, and the next core migration is what breaks.

A declared location that does not exist stops the command: a silently missing extension schema
would give a platform that starts and fails at the first request touching its tables.
"""

from __future__ import annotations

import os
import pathlib
import sys
from importlib import metadata

from alembic import command
from alembic.config import Config

#: The entry-point group where an extension declares its migrations. The value it points to is a
#: path (`str` or `pathlib.Path`) to a directory of Alembic revisions.
MIGRATIONS_GROUP = "choregos.migrations"

MIGRATIONS = pathlib.Path(__file__).resolve().parent / "migrations"


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
