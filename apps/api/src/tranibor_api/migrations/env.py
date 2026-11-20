# SPDX-License-Identifier: Apache-2.0
"""Alembic environment: async migrations, schema read from the models."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from tranibor_api.config import get_settings
from tranibor_api.db.base import Base
from tranibor_api.db.models import ALL_TABLES, is_core_object  # noqa: F401 - imports every table
from tranibor_api.logging import get_logger
from tranibor_api.migrate import forget_retired_revisions
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    # `fileConfig` disables every logger that already exists and is not named in the ini (its default).
    # The migration Job does not care, but a process that migrates and then goes on (a test suite, an
    # embedded start-up) lost `temporalio.workflow` and the like: the log line a replay test waits for
    # never came, depending on which xdist worker ran the migration first (#2008).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=is_core_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # The rows of a branch whose package was removed: Alembic cannot read them (ADR 0181, O4-1).
    with connection.begin():
        forgotten = forget_retired_revisions(connection, context.script)
    if forgotten:
        get_logger("choregos.migrate").warning("retired_revisions_forgotten", revisions=forgotten)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=is_core_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _url()
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
