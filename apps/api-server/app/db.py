"""
Подключение к PostgreSQL и Redis.

Один движок (engine) на процесс, сессии создаются на каждый запрос/обработчик.
Правило: сессия живёт ровно столько, сколько длится одна операция, и всегда
закрывается — иначе пул соединений закончится на 200-м пользователе.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()


def _engine_kwargs() -> dict:
    """
    Параметры движка.

    У SQLite нет пула соединений в привычном смысле, поэтому настройки пула
    применяются только к Postgres — иначе локальный запуск падает с ошибкой.
    """
    if settings.local_mode:
        # Бот и API — разные процессы, а файл базы один. Увеличенный таймаут
        # плюс журнал WAL (включается ниже) позволяют им писать одновременно
        # без ошибки "database is locked".
        return {"echo": False, "connect_args": {"timeout": 30}}
    return {
        "echo": False,
        "pool_size": 20,  # запас на 1000 пользователей с большим запасом
        "max_overflow": 10,
        "pool_pre_ping": True,  # проверять живость соединения перед выдачей
        "pool_recycle": 1800,  # пересоздавать соединения раз в 30 минут
    }


engine = create_async_engine(settings.database_url, **_engine_kwargs())

if settings.local_mode:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
        """
        Режим WAL: читатели не блокируют писателя.

        Нужен, когда в локальном режиме подняты сразу бот и API — иначе
        второй процесс падает на "database is locked".
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # объекты остаются доступны после commit()
    autoflush=False,
)

_redis: Redis | None = None


def get_redis() -> Redis:
    """Ленивое подключение к Redis — один клиент на процесс."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """
    Транзакция на блок кода.

        async with session_scope() as session:
            ...работа с БД...

    При исключении — откат, при выходе — commit и закрытие.
    """
    session = SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость для FastAPI: Depends(get_session)."""
    async with session_scope() as session:
        yield session


async def close_connections() -> None:
    """Аккуратное закрытие при остановке процесса."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
    await engine.dispose()


async def create_all_tables() -> None:
    """
    Создать таблицы напрямую, без Alembic.

    Используется только в локальном режиме (SQLite), чтобы бот запускался
    одной командой. На проде схемой управляют миграции: make migrate.
    """
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
