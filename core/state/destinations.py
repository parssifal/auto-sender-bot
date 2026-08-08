from __future__ import annotations

import time

import core.limits as limits
from core.limits import ResourceLimitError
from core.state.base import locked_write
from core.state.models import Destination


class DestinationsMixin:
    @locked_write
    async def upsert_destination(
        self,
        chat_id: int,
        type_: str,
        title: str,
        username: str | None,
        bot_status: str,
        bot_can_post: bool | None,
    ) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO destinations(chat_id, type, title, username, bot_status, bot_can_post, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                type=excluded.type,
                title=excluded.title,
                username=excluded.username,
                bot_status=excluded.bot_status,
                bot_can_post=excluded.bot_can_post,
                updated_at=excluded.updated_at
            """,
            (chat_id, type_, title, username, bot_status, int(bot_can_post) if bot_can_post is not None else None, now),
        )
        await self._conn.commit()

    @locked_write
    async def link_user_destination(self, user_id: int, chat_id: int, linked_via: str) -> None:
        # Only NEW links count toward the cap; re-linking an existing destination
        # is an upsert (no growth) and must always be allowed.
        existing = await self._execute_fetchone(
            "SELECT 1 FROM user_destinations WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        )
        if existing is None and await self.count_user_destinations(user_id) >= limits.MAX_DESTINATIONS_PER_USER:
            raise ResourceLimitError("destinations", limits.MAX_DESTINATIONS_PER_USER)

        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO user_destinations(user_id, chat_id, linked_via, linked_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                linked_via=excluded.linked_via,
                linked_at=excluded.linked_at
            """,
            (user_id, chat_id, linked_via, now),
        )
        await self._conn.commit()

    async def count_user_destinations(self, user_id: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM user_destinations WHERE user_id=?",
            (user_id,),
        )
        return 0 if row is None else int(row["cnt"])

    async def list_user_destinations(self, user_id: int, offset: int, limit: int) -> list[Destination]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT d.chat_id, d.type, d.title, d.username, d.bot_status, d.bot_can_post
            FROM user_destinations ud
            JOIN destinations d ON d.chat_id = ud.chat_id
            WHERE ud.user_id=?
            ORDER BY d.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        out: list[Destination] = []
        for r in rows:
            out.append(
                Destination(
                    chat_id=int(r["chat_id"]),
                    type=str(r["type"]),
                    title=str(r["title"]),
                    username=r["username"],
                    bot_status=str(r["bot_status"]),
                    bot_can_post=None if r["bot_can_post"] is None else bool(int(r["bot_can_post"])),
                )
            )
        return out

    async def get_destination_title(self, chat_id: int) -> str | None:
        row = await self._execute_fetchone(
            "SELECT title FROM destinations WHERE chat_id=?",
            (chat_id,),
        )
        return None if row is None else str(row["title"])
