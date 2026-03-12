from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context
from backend.models.db import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use our ORM Base metadata for autogenerate support
target_metadata = Base.metadata


def _resolve_url() -> str:
    """Resolve the database URL, preferring programmatic override."""
    url = config.get_main_option("sqlalchemy.url", "")
    if not url or url == "sqlite:///tower_data.db":
        from backend.config import DEFAULT_DB_PATH

        return f"sqlite:///{DEFAULT_DB_PATH}"
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    from sqlalchemy import create_engine as sa_create_engine

    url = _resolve_url()
    connectable = sa_create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
