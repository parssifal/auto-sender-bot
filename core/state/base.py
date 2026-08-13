from __future__ import annotations

import asyncio
import functools
from typing import Awaitable, Callable, TypeVar

import aiosqlite

from core.migrate import run_migrations

_T = TypeVar("_T")


def locked_write(method: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
    """Serialize a write method against every other write on the shared connection.

    One aiosqlite connection is shared by the scheduler, the polling handlers, the
    Mini App and the healthcheck. Without this, an explicit ``BEGIN IMMEDIATE``
    lands inside another method's implicit transaction ("cannot start a
    transaction within a transaction"), and a foreign ``commit()`` commits a
    half-applied write that its own ``rollback()`` can then no longer undo.

    ponytail: global lock, serializes all writes; upgrade path is a separate
    connection per worker if throughput ever matters.

    ``asyncio.Lock`` is NOT reentrant: never decorate a method that awaits another
    decorated method (the thin delegator ``update_editable_post_time`` stays
    undecorated for that reason). Read-only methods stay unlocked — locked writes
    call them internally.
    """

    @functools.wraps(method)
    async def wrapper(self: "StateStoreBase", *args: object, **kwargs: object) -> _T:
        async with self._write_lock:
            return await method(self, *args, **kwargs)

    return wrapper


class StateStoreBase:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn
        self._write_lock = asyncio.Lock()

    async def _execute_fetchone(self, query: str, params: tuple[object, ...] = ()) -> aiosqlite.Row | None:
        cur = await self._conn.execute(query, params)
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def migrate(self) -> None:
        await run_migrations(self._conn)
        await self._reconcile_user_columns()
        await self._reconcile_scheduled_post_columns()
        await self._requeue_stuck_sending()

    async def _requeue_stuck_sending(self) -> None:
        # Only the scheduler claims posts, and only one process runs it, so any
        # 'sending' row at startup is a crash between claim and mark_* — it would
        # otherwise stay invisible, un-cancellable and hold a quota slot forever.
        # attempts stays as claim left it, so retry counting survives the crash.
        await self._conn.execute("UPDATE scheduled_posts SET status='pending' WHERE status='sending'")
        await self._conn.commit()

    async def _reconcile_user_columns(self) -> None:
        # Legacy-DB safety net: the baseline path skips DDL, so ensure the
        # columns folded into migration 001 also exist on pre-migration DBs.
        user_columns = await self._conn.execute_fetchall("PRAGMA table_info(users)")
        user_column_names = {str(row["name"]) for row in user_columns}
        if "language" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN language TEXT NULL")
        if "username" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN username TEXT NULL")
        if "first_name" not in user_column_names:
            await self._conn.execute("ALTER TABLE users ADD COLUMN first_name TEXT NULL")
        await self._conn.commit()

    async def _reconcile_scheduled_post_columns(self) -> None:
        # Legacy-DB safety net: baseline stamps existing tables without running
        # DDL, so later columns must be reconciled explicitly.
        post_columns = await self._conn.execute_fetchall("PRAGMA table_info(scheduled_posts)")
        post_column_names = {str(row["name"]) for row in post_columns}
        if "request_id" not in post_column_names:
            await self._conn.execute("ALTER TABLE scheduled_posts ADD COLUMN request_id TEXT NULL")
        await self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_user_request_id
            ON scheduled_posts(user_id, request_id)
            WHERE request_id IS NOT NULL
            """
        )
        await self._conn.commit()
