from __future__ import annotations

import time


class UsersMixin:
    async def ensure_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO users(user_id, timezone, language, username, first_name, created_at, updated_at)
            VALUES(?, NULL, NULL, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name)
            """,
            (user_id, username, first_name, now, now),
        )
        await self._conn.commit()

    async def get_user_timezone(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT timezone FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["timezone"]

    async def set_user_timezone(self, user_id: int, tz_name: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET timezone=?, updated_at=? WHERE user_id=?",
            (tz_name, now, user_id),
        )
        await self._conn.commit()

    async def get_user_language(self, user_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT language FROM users WHERE user_id=?",
            (user_id,),
        )
        return None if row is None else row["language"]

    async def set_user_language(self, user_id: int, language: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            "UPDATE users SET language=?, updated_at=? WHERE user_id=?",
            (language, now, user_id),
        )
        await self._conn.commit()

    async def all_user_ids(self) -> list[int]:
        rows = await self._conn.execute_fetchall("SELECT user_id FROM users ORDER BY user_id")
        return [int(r["user_id"]) for r in rows]
