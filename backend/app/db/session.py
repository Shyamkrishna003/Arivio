"""
Database session management using async SQLAlchemy.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings
from app.db.url import normalize_database_url

settings = get_settings()

# A managed provider's URL is libpq-shaped and asyncpg rejects it as given.
_url, _connect_args = normalize_database_url(
    settings.DATABASE_URL,
    prepared_statements=settings.DATABASE_PREPARED_STATEMENTS,
)

engine = create_async_engine(
    _url,
    echo=settings.DATABASE_ECHO,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    # A provider that suspends an idle database returns connections that still
    # look open and fail on first use. pool_pre_ping already catches those one
    # at a time; recycling stops the pool from accumulating a full set of them.
    pool_recycle=300,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


async def get_db() -> AsyncSession:
    """Dependency that provides an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
