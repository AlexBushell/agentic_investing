"""Database engine, session, and migration helpers for the company data store."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from research_platform.core.config import Settings


def create_store_engine(settings: Settings) -> Engine:
    """Create the SQLAlchemy engine for the configured store database."""
    return create_engine(build_sync_database_url(settings.database_url), future=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    """Create a session factory bound to the store engine."""
    engine = create_store_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, future=True)


def run_migrations_to_head(settings: Settings) -> None:
    """Apply Alembic migrations to the configured database."""
    alembic_cfg = Config(str(Path("alembic.ini")))
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        render_sync_database_url(settings.database_url),
    )
    command.upgrade(alembic_cfg, "head")


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    """Provide a transactional session scope for store operations."""
    session_factory = create_session_factory(settings)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_sync_database_url(database_url: str) -> URL:
    """Rewrite async PostgreSQL URLs to a sync SQLAlchemy URL object."""
    url = make_url(database_url)
    if url.drivername == "postgresql+asyncpg":
        return url.set(drivername="postgresql+psycopg")
    return url


def render_sync_database_url(database_url: str) -> str:
    """Render a sync SQLAlchemy URL as a string for tools that require one."""
    return build_sync_database_url(database_url).render_as_string(hide_password=False)
