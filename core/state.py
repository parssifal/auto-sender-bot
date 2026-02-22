from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import aiosqlite


@dataclass(frozen=True)
class Destination:
    chat_id: int
    type: str
    title: str
    username: str | None
    bot_status: str
    bot_can_post: bool | None


@dataclass(frozen=True)
class ScheduledPostRow:
    id: str
    user_id: int
    chat_id: int
    scheduled_at_utc: int
    status: str
    kind: str
    text: str | None
    entities_json: str | None
    caption: str | None
    caption_entities_json: str | None
    caption_above: int | None
    attempts: int
    next_retry_at_utc: int | None
    created_at: int
    sent_at: int | None
    last_error: str | None


class StateStore:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS destinations (
                chat_id INTEGER PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                username TEXT NULL,
                bot_status TEXT NOT NULL,
                bot_can_post INTEGER NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_destinations (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                linked_via TEXT NOT NULL,
                linked_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                scheduled_at_utc INTEGER NOT NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NULL,
                entities_json TEXT NULL,
                caption TEXT NULL,
                caption_entities_json TEXT NULL,
                caption_above INTEGER NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at_utc INTEGER NULL,
                created_at INTEGER NOT NULL,
                sent_at INTEGER NULL,
                last_error TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_scheduled_due
                ON scheduled_posts(status, scheduled_at_utc, next_retry_at_utc);

            CREATE TABLE IF NOT EXISTS scheduled_post_media (
                post_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (post_id, idx),
                FOREIGN KEY (post_id) REFERENCES scheduled_posts(id) ON DELETE CASCADE
            );
            """
        )
        await self._conn.commit()

    async def ensure_user(self, user_id: int) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO users(user_id, timezone, created_at, updated_at)
            VALUES(?, NULL, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (user_id, now, now),
        )
        await self._conn.commit()

    async def get_user_timezone(self, user_id: int) -> str | None:
        row = await self._conn.execute_fetchone(
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

    async def link_user_destination(self, user_id: int, chat_id: int, linked_via: str) -> None:
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
        row = await self._conn.execute_fetchone(
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

    async def create_scheduled_text_post(
        self,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        text: str,
        entities_json: str | None,
    ) -> str:
        now = int(time.time())
        post_id = uuid.uuid4().hex
        await self._conn.execute(
            """
            INSERT INTO scheduled_posts(
                id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                attempts, next_retry_at_utc, created_at
            ) VALUES(?, ?, ?, ?, 'pending', 'text', ?, ?, 0, NULL, ?)
            """,
            (post_id, user_id, chat_id, scheduled_at_utc, text, entities_json, now),
        )
        await self._conn.commit()
        return post_id

    async def create_scheduled_media_post(
        self,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]],
    ) -> str:
        now = int(time.time())
        post_id = uuid.uuid4().hex
        await self._conn.execute(
            """
            INSERT INTO scheduled_posts(
                id, user_id, chat_id, scheduled_at_utc, status, kind,
                caption, caption_entities_json, caption_above,
                attempts, next_retry_at_utc, created_at
            ) VALUES(?, ?, ?, ?, 'pending', 'media', ?, ?, ?, 0, NULL, ?)
            """,
            (
                post_id,
                user_id,
                chat_id,
                scheduled_at_utc,
                caption,
                caption_entities_json,
                None if caption_above is None else int(caption_above),
                now,
            ),
        )
        for idx, item in enumerate(media_items):
            await self._conn.execute(
                "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                (post_id, idx, item["type"], item["file_id"]),
            )
        await self._conn.commit()
        return post_id

    async def list_pending_posts(self, user_id: int, limit: int = 10) -> list[ScheduledPostRow]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM scheduled_posts
            WHERE user_id=? AND status='pending'
            ORDER BY scheduled_at_utc ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._row_to_post(r) for r in rows]

    async def cancel_post(self, user_id: int, post_id: str) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='cancelled'
            WHERE id=? AND user_id=? AND status='pending'
            """,
            (post_id, user_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def list_due_posts(self, now_utc: int, limit: int = 10) -> list[ScheduledPostRow]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM scheduled_posts
            WHERE status='pending'
              AND scheduled_at_utc <= ?
              AND (next_retry_at_utc IS NULL OR next_retry_at_utc <= ?)
            ORDER BY scheduled_at_utc ASC
            LIMIT ?
            """,
            (now_utc, now_utc, limit),
        )
        return [self._row_to_post(r) for r in rows]

    async def claim_post_for_sending(self, post_id: str, now_utc: int) -> bool:
        cur = await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='sending', attempts=attempts+1, last_error=NULL
            WHERE id=?
              AND status='pending'
              AND (next_retry_at_utc IS NULL OR next_retry_at_utc <= ?)
            """,
            (post_id, now_utc),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def mark_sent(self, post_id: str, sent_at_utc: int) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='sent', sent_at=?, next_retry_at_utc=NULL
            WHERE id=? AND status='sending'
            """,
            (sent_at_utc, post_id),
        )
        await self._conn.commit()

    async def mark_retry(self, post_id: str, next_retry_at_utc: int, error: str) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='pending', next_retry_at_utc=?, last_error=?
            WHERE id=? AND status='sending'
            """,
            (next_retry_at_utc, error, post_id),
        )
        await self._conn.commit()

    async def mark_failed(self, post_id: str, error: str) -> None:
        await self._conn.execute(
            """
            UPDATE scheduled_posts
            SET status='failed', last_error=?
            WHERE id=? AND status='sending'
            """,
            (error, post_id),
        )
        await self._conn.commit()

    async def get_post_media(self, post_id: str) -> list[dict[str, str]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT idx, type, file_id
            FROM scheduled_post_media
            WHERE post_id=?
            ORDER BY idx ASC
            """,
            (post_id,),
        )
        out: list[dict[str, str]] = []
        for r in rows:
            out.append({"type": str(r["type"]), "file_id": str(r["file_id"])})
        return out

    async def get_destination_title(self, chat_id: int) -> str | None:
        row = await self._conn.execute_fetchone(
            "SELECT title FROM destinations WHERE chat_id=?",
            (chat_id,),
        )
        return None if row is None else str(row["title"])

    @staticmethod
    def dump_entities(entities: Iterable[Any] | None) -> str | None:
        if not entities:
            return None
        return json.dumps([e.model_dump() for e in entities], ensure_ascii=False)

    @staticmethod
    def _row_to_post(row: aiosqlite.Row) -> ScheduledPostRow:
        return ScheduledPostRow(
            id=str(row["id"]),
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            scheduled_at_utc=int(row["scheduled_at_utc"]),
            status=str(row["status"]),
            kind=str(row["kind"]),
            text=row["text"],
            entities_json=row["entities_json"],
            caption=row["caption"],
            caption_entities_json=row["caption_entities_json"],
            caption_above=row["caption_above"],
            attempts=int(row["attempts"]),
            next_retry_at_utc=row["next_retry_at_utc"],
            created_at=int(row["created_at"]),
            sent_at=row["sent_at"],
            last_error=row["last_error"],
        )
