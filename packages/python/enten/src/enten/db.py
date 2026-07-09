"""SQLAlchemy 2.0 database helpers for the enten Python core."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from enten.config import Settings

Base = declarative_base()

_settings = Settings()


def get_engine():
    """Return the async SQLAlchemy engine for the configured database URL."""
    return create_async_engine(_settings.database_url, future=True, echo=False)


def get_session_factory():
    """Return an async session factory bound to the configured engine."""
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session and rollback on unhandled exceptions."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
