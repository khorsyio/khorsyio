from __future__ import annotations

import re
from typing import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from khorsyio.core.settings import settings


class Base(DeclarativeBase):
    """Base class for ORM models."""
    pass


def _normalize_dsn(dsn: str) -> str:
    """Ensure DSN is in SQLAlchemy async form (postgresql+asyncpg://...).
    If already in correct form return as is. If in asyncpg/psycopg style, try to adapt.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    # Accept plain postgresql:// and upgrade to async driver
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://"):]
    # Fallback: try to replace common schemes
    return re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", dsn)


class SA:
    """SQLAlchemy async components container.

    Provides configured AsyncEngine and async_sessionmaker.
    """

    def __init__(self, dsn: str | None = None, pool_min: int | None = None, pool_max: int | None = None):
        self.dsn = _normalize_dsn(dsn or settings.db.dsn)
        # Map min/max to SQLAlchemy pool_size/max_overflow
        min_size = pool_min if pool_min is not None else settings.db.pool_min
        max_size = pool_max if pool_max is not None else settings.db.pool_max
        pool_size = max(0, int(min_size))
        max_overflow = max(0, int(max_size) - pool_size)

        self.engine: AsyncEngine = create_async_engine(
            self.dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            future=True,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide an async session context manager.

        Example:
            async with sa.session() as s:
                await s.execute(sa_text("select 1"))
        """
        session: AsyncSession = self.session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
