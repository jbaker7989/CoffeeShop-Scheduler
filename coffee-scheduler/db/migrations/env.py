"""Alembic environment for the Coffee Shop Scheduler Postgres data plane.

The URL comes from DATABASE_URL (never committed). Default points at the local
docker-compose Postgres (F43). Migrations run as the DB superuser (`coffeeshop`),
which bypasses RLS — RLS is enforced for the non-owner app_rw / app_ro roles.
"""
import os
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://coffeeshop:coffeeshop@localhost:5432/coffeeshop",
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
