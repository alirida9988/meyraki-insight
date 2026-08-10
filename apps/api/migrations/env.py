"""Alembic environment, wired to the application's own settings and metadata.

The URL comes from `app.settings`, never from alembic.ini: one source of truth means a
migration can never be applied to a different database than the one the app talks to.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  — importing registers every table on Base.metadata
from app.db import Base
from app import settings

config = context.config
# `%` doubled because set_main_option writes into a ConfigParser, which reads a lone `%`
# as interpolation syntax and raises. Passwords reach us percent-encoded — a `@` in a
# password MUST be written `%40` or it terminates the userinfo and the host is parsed
# wrong — so an encoded password would crash Alembic before it opened a connection.
# The API image runs `alembic upgrade head` on start, so this failed the container's
# boot, not just a developer's command. Found against a real managed database whose
# generated password contained `@`.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type so a column changing from String(50) to Text is caught;
            # without it Alembic only notices added and dropped columns.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
