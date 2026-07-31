from __future__ import annotations

import time

import aiosqlite

from core.migrate import run_migrations


class StateStoreBase:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def _execute_fetchone(self, query: str, params: tuple[object, ...] = ()) -> aiosqlite.Row | None:
        cur = await self._conn.execute(query, params)
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def migrate(self) -> None:
        await run_migrations(self._conn)
        await self._reconcile_user_columns()
        await self._backfill_team_owners()

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

    async def _backfill_team_owners(self) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            UPDATE team_members
            SET role='owner', updated_at=?
            WHERE (team_id, user_id) IN (
                SELECT id, owner_user_id
                FROM teams
            )
              AND role <> 'owner'
            """,
            (now,),
        )
        await self._conn.execute(
            """
            INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
            SELECT t.id, t.owner_user_id, 'owner', ?, ?
            FROM teams t
            WHERE NOT EXISTS (
                SELECT 1
                FROM team_members tm
                WHERE tm.team_id = t.id
                  AND tm.user_id = t.owner_user_id
            )
            """,
            (now, now),
        )
        await self._conn.commit()
