import logging
import re
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from khorsyio.core.settings import settings

log = logging.getLogger("khorsyio.db")


def _normalize_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://"):]
    return re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", dsn)


def _translate_placeholders(sql: str, args: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
    """Convert $1, $2, ... placeholders (asyncpg style) into :p1, :p2 for SQLAlchemy.
    Returns new SQL and a dict of parameters.
    If SQL already contains ':' style, leave unchanged and map positionally to :p1... too.
    """
    if "$1" in sql:
        # Replace $n with :p{n}
        def repl(m):
            n = m.group(1)
            return f":p{n}"
        sql2 = re.sub(r"\$(\d+)", repl, sql)
        params = {f"p{i+1}": v for i, v in enumerate(args)}
        return sql2, params
    # No $n placeholders: assume positional, assign :p1.. in order for safety
    params = {f"p{i+1}": v for i, v in enumerate(args)}
    # Only add binds if not already contains ":" binds
    if ":" not in sql:
        # naive insert binds by replacing '?' if present
        if "?" in sql:
            i = 0
            def qrepl(_):
                nonlocal i
                i += 1
                return f":p{i}"
            sql = re.sub(r"\?", qrepl, sql)
    return sql, params


class Database:
    def __init__(self, dsn: str | None = None):
        self._dsn = _normalize_dsn(dsn or settings.db.dsn)
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self):
        min_size = settings.db.pool_min
        max_size = settings.db.pool_max
        pool_size = max(0, int(min_size))
        max_overflow = max(0, int(max_size) - pool_size)
        self._engine = create_async_engine(
            self._dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            future=True,
        )
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False, class_=AsyncSession)
        log.info(f"db connected pool={settings.db.pool_min}-{settings.db.pool_max}")

    async def close(self):
        if self._engine is not None:
            await self._engine.dispose()
            log.info("db closed")

    async def fetch(self, query: str, *args) -> list[dict]:
        assert self._engine is not None
        sql, params = _translate_placeholders(query, args)
        async with self._engine.begin() as conn:
            res = await conn.execute(text(sql), params)
            rows = res.mappings().all()
            return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args) -> dict | None:
        assert self._engine is not None
        sql, params = _translate_placeholders(query, args)
        async with self._engine.begin() as conn:
            res = await conn.execute(text(sql), params)
            row = res.mappings().first()
            return dict(row) if row else None

    async def fetchval(self, query: str, *args):
        assert self._engine is not None
        sql, params = _translate_placeholders(query, args)
        async with self._engine.begin() as conn:
            res = await conn.execute(text(sql), params)
            one = res.scalar_one_or_none()
            return one

    async def execute(self, query: str, *args) -> str:
        assert self._engine is not None
        sql, params = _translate_placeholders(query, args)
        async with self._engine.begin() as conn:
            res = await conn.execute(text(sql), params)
            # emulate asyncpg return string like "INSERT 0 1" using rowcount
            return f"OK {res.rowcount or 0}"

    async def executemany(self, query: str, args: list) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            # Convert args list of tuples into list of dicts using :p1.. pattern
            if not args:
                return
            sql, _ = _translate_placeholders(query, tuple())
            if "$1" in query:
                param_sets = []
                for tpl in args:
                    params = {f"p{i+1}": v for i, v in enumerate(tpl)}
                    param_sets.append(params)
            else:
                # If named binds already present, assume args is list of dicts
                if isinstance(args[0], dict):
                    param_sets = args
                else:
                    param_sets = [
                        {f"p{i+1}": v for i, v in enumerate(tpl)} for tpl in args
                    ]
            await conn.execute(text(sql), param_sets)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an AsyncSession bound to the shared engine.
        Commits on success and rollbacks on error.
        """
        assert self._session_factory is not None, "Database.connect() must be called before using sessions"
        session: AsyncSession = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
