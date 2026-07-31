"""Temporary holding module for not-yet-extracted StateStore domains.

This mixin shrinks as each domain (users, destinations, teams, drafts,
posts, recurring, stats) is split into its own dedicated module. Methods
here are moved verbatim; nothing in this file should be modified beyond
deletion once a domain is extracted.
"""

from __future__ import annotations

import time
import uuid

from core.state.models import (
    RecurringInstance,
    RecurringPattern,
    RecurringPatternSummary,
)


class _LegacyMixin:
    # ------------------------------------------------------------------
    # Admin statistics (read-only aggregates)
    # ------------------------------------------------------------------
    async def count_users(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM users", ())
        return 0 if row is None else int(row["cnt"])

    async def avg_destinations_per_user(self) -> float:
        users = await self.count_users()
        if users == 0:
            return 0.0
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM user_destinations", ())
        links = 0 if row is None else int(row["cnt"])
        return links / users

    async def count_new_users(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM users WHERE created_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def count_active_users(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM scheduled_posts WHERE created_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def count_posts_by_status(self) -> dict[str, int]:
        counts = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "cancelled": 0}
        rows = await self._conn.execute_fetchall(
            "SELECT status, COUNT(1) AS cnt FROM scheduled_posts GROUP BY status",
            (),
        )
        for r in rows:
            counts[str(r["status"])] = int(r["cnt"])
        return counts

    async def count_posts_sent_since(self, since_ts: int) -> int:
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM scheduled_posts WHERE status='sent' AND sent_at >= ?",
            (since_ts,),
        )
        return 0 if row is None else int(row["cnt"])

    async def language_distribution(self) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT COALESCE(language, 'unknown') AS lang, COUNT(1) AS cnt
            FROM users
            GROUP BY COALESCE(language, 'unknown')
            ORDER BY cnt DESC, lang ASC
            """,
            (),
        )
        return [(str(r["lang"]), int(r["cnt"])) for r in rows]

    async def count_teams(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM teams", ())
        return 0 if row is None else int(row["cnt"])

    async def count_drafts(self) -> int:
        row = await self._execute_fetchone("SELECT COUNT(1) AS cnt FROM drafts", ())
        return 0 if row is None else int(row["cnt"])

    async def top_active_users(self, limit: int, since_ts: int) -> list[tuple[int, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT user_id, COUNT(1) AS cnt
            FROM scheduled_posts
            WHERE created_at >= ?
            GROUP BY user_id
            ORDER BY cnt DESC, user_id ASC
            LIMIT ?
            """,
            (since_ts, limit),
        )
        return [(int(r["user_id"]), int(r["cnt"])) for r in rows]

    async def list_users(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT
                u.user_id,
                u.username,
                u.first_name,
                u.language,
                u.created_at,
                u.updated_at AS last_active,
                (SELECT COUNT(1) FROM scheduled_posts sp WHERE sp.user_id = u.user_id) AS posts,
                (SELECT COUNT(1) FROM user_destinations ud WHERE ud.user_id = u.user_id) AS channels
            FROM users u
            ORDER BY u.updated_at DESC, u.user_id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [
            {
                "user_id": int(r["user_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "language": r["language"],
                "created_at": int(r["created_at"]),
                "last_active": int(r["last_active"]),
                "posts": int(r["posts"]),
                "channels": int(r["channels"]),
            }
            for r in rows
        ]

    async def get_user_profile(self, user_id: int) -> dict[str, object] | None:
        row = await self._execute_fetchone(
            "SELECT user_id, timezone, language, username, first_name, created_at FROM users WHERE user_id=?",
            (user_id,),
        )
        if row is None:
            return None
        channels = await self.count_user_destinations(user_id)
        posts_row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM scheduled_posts WHERE user_id=?",
            (user_id,),
        )
        posts = 0 if posts_row is None else int(posts_row["cnt"])
        status_rows = await self._conn.execute_fetchall(
            "SELECT status, COUNT(1) AS cnt FROM scheduled_posts WHERE user_id=? GROUP BY status",
            (user_id,),
        )
        posts_by_status = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "cancelled": 0}
        for r in status_rows:
            posts_by_status[str(r["status"])] = int(r["cnt"])
        return {
            "user_id": int(row["user_id"]),
            "timezone": row["timezone"],
            "language": row["language"],
            "username": row["username"],
            "first_name": row["first_name"],
            "created_at": int(row["created_at"]),
            "channels": channels,
            "posts": posts,
            "posts_by_status": posts_by_status,
        }

    async def daily_new_users(self, since_ts: int) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') AS day, COUNT(1) AS cnt
            FROM users
            WHERE created_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_ts,),
        )
        return [(str(r["day"]), int(r["cnt"])) for r in rows]

    async def daily_posts_sent(self, since_ts: int) -> list[tuple[str, int]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT strftime('%Y-%m-%d', sent_at, 'unixepoch') AS day, COUNT(1) AS cnt
            FROM scheduled_posts
            WHERE status='sent' AND sent_at >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (since_ts,),
        )
        return [(str(r["day"]), int(r["cnt"])) for r in rows]

    async def create_recurring_pattern(
        self,
        user_id: int,
        chat_id: int,
        interval_type: str,
        time_of_day_minutes: int,
        timezone: str,
        start_at_utc: int,
        *,
        weekdays_mask: int | None = None,
        end_at_utc: int | None = None,
        max_occurrences: int | None = None,
        current_count: int = 0,
        is_active: bool = True,
    ) -> str:
        now = int(time.time())
        pattern_id = uuid.uuid4().hex
        await self._conn.execute(
            """
            INSERT INTO recurring_patterns(
                id,
                user_id,
                chat_id,
                interval_type,
                weekdays_mask,
                time_of_day_minutes,
                timezone,
                start_at_utc,
                end_at_utc,
                max_occurrences,
                current_count,
                is_active,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id,
                user_id,
                chat_id,
                interval_type,
                weekdays_mask,
                time_of_day_minutes,
                timezone,
                start_at_utc,
                end_at_utc,
                max_occurrences,
                current_count,
                int(is_active),
                now,
                now,
            ),
        )
        await self._conn.commit()
        return pattern_id

    async def get_recurring_pattern(self, pattern_id: str) -> "RecurringPattern | None":
        row = await self._execute_fetchone(
            "SELECT * FROM recurring_patterns WHERE id=?",
            (pattern_id,),
        )
        return None if row is None else self._row_to_recurring_pattern(row)

    async def list_user_recurring(self, user_id: int, include_inactive: bool = False) -> list["RecurringPattern"]:
        query = """
            SELECT *
            FROM recurring_patterns
            WHERE user_id=?
        """
        params: list[object] = [user_id]
        if not include_inactive:
            query += " AND is_active=1"
        query += " ORDER BY created_at DESC, id ASC"

        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_recurring_pattern(row) for row in rows]

    async def list_user_recurring_summaries(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 10,
        include_inactive: bool = False,
    ) -> list["RecurringPatternSummary"]:
        query = """
            SELECT
                rp.*,
                d.title AS destination_title,
                d.username AS destination_username,
                (
                    SELECT sp.id
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_post_id,
                (
                    SELECT sp.scheduled_at_utc
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_scheduled_at_utc,
                (
                    SELECT sp.status
                    FROM recurring_instances ri
                    JOIN scheduled_posts sp ON sp.id = ri.post_id
                    WHERE ri.pattern_id = rp.id
                      AND sp.status IN ('pending', 'sending')
                    ORDER BY
                        CASE sp.status WHEN 'pending' THEN 0 ELSE 1 END,
                        ri.ordinal ASC
                    LIMIT 1
                ) AS next_post_status
            FROM recurring_patterns rp
            JOIN destinations d ON d.chat_id = rp.chat_id
            WHERE rp.user_id=?
        """
        params: list[object] = [user_id]
        if not include_inactive:
            query += " AND rp.is_active=1"
        query += " ORDER BY rp.created_at DESC, rp.id ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))

        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_recurring_summary(row) for row in rows]

    async def update_recurring_count(self, pattern_id: str, new_count: int) -> bool:
        now = int(time.time())
        cur = await self._conn.execute(
            """
            UPDATE recurring_patterns
            SET current_count=?, updated_at=?
            WHERE id=?
            """,
            (new_count, now, pattern_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def delete_recurring_pattern(self, pattern_id: str) -> bool:
        now = int(time.time())
        cur = await self._conn.execute(
            """
            UPDATE recurring_patterns
            SET is_active=0, updated_at=?
            WHERE id=? AND is_active=1
            """,
            (now, pattern_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def cancel_recurring_pattern(self, user_id: int, pattern_id: str) -> bool:
        now = int(time.time())
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._execute_fetchone(
                "SELECT id FROM recurring_patterns WHERE id=? AND user_id=?",
                (pattern_id, user_id),
            )
            if row is None:
                await self._conn.rollback()
                return False

            await self._conn.execute(
                """
                UPDATE recurring_patterns
                SET is_active=0, updated_at=?
                WHERE id=? AND user_id=?
                """,
                (now, pattern_id, user_id),
            )
            await self._conn.execute(
                """
                UPDATE scheduled_posts
                SET status='cancelled'
                WHERE user_id=?
                  AND status='pending'
                  AND id IN (
                      SELECT post_id
                      FROM recurring_instances
                      WHERE pattern_id=?
                  )
                """,
                (user_id, pattern_id),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def create_recurring_instance(
        self,
        pattern_id: str,
        post_id: str,
        ordinal: int,
        scheduled_for_utc: int,
    ) -> RecurringInstance:
        created_at = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (pattern_id, post_id, ordinal, scheduled_for_utc, created_at),
        )
        await self._conn.commit()
        return RecurringInstance(
            pattern_id=pattern_id,
            post_id=post_id,
            ordinal=ordinal,
            scheduled_for_utc=scheduled_for_utc,
            created_at=created_at,
        )

    async def materialize_next_recurring_post(
        self,
        pattern_id: str,
        source_post_id: str,
        next_ordinal: int,
        scheduled_for_utc: int,
    ) -> RecurringInstance | None:
        source_post = await self.get_scheduled_post(source_post_id)
        if source_post is None:
            raise ValueError(f"Scheduled post {source_post_id} not found")

        media_items = await self.get_post_media(source_post_id) if source_post.kind == "media" else []
        now = int(time.time())
        next_post_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            pattern_row = await self._execute_fetchone(
                "SELECT is_active FROM recurring_patterns WHERE id=?",
                (pattern_id,),
            )
            if pattern_row is None or not bool(int(pattern_row["is_active"])):
                await self._conn.rollback()
                return None

            await self._conn.execute(
                """
                INSERT INTO scheduled_posts(
                    id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                    caption, caption_entities_json, caption_above,
                    attempts, next_retry_at_utc, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    next_post_id,
                    source_post.user_id,
                    source_post.chat_id,
                    scheduled_for_utc,
                    source_post.kind,
                    source_post.text,
                    source_post.entities_json,
                    source_post.caption,
                    source_post.caption_entities_json,
                    source_post.caption_above,
                    now,
                ),
            )
            for idx, item in enumerate(media_items):
                await self._conn.execute(
                    "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (next_post_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.execute(
                """
                INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (pattern_id, next_post_id, next_ordinal, scheduled_for_utc, now),
            )
            cur = await self._conn.execute(
                """
                UPDATE recurring_patterns
                SET current_count=?, updated_at=?
                WHERE id=? AND is_active=1
                """,
                (next_ordinal, now, pattern_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"Recurring pattern {pattern_id} is missing or inactive")

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return RecurringInstance(
            pattern_id=pattern_id,
            post_id=next_post_id,
            ordinal=next_ordinal,
            scheduled_for_utc=scheduled_for_utc,
            created_at=now,
        )

    async def create_recurring_series(
        self,
        *,
        user_id: int,
        chat_id: int,
        interval_type: str,
        time_of_day_minutes: int,
        timezone: str,
        start_at_utc: int,
        kind: str,
        weekdays_mask: int | None = None,
        end_at_utc: int | None = None,
        max_occurrences: int | None = None,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        if kind not in {"text", "media"}:
            raise ValueError(f"Unsupported recurring post kind: {kind}")

        items = list(media_items or [])
        if kind == "media" and not items:
            raise ValueError("Recurring media post must contain at least one media item")

        now = int(time.time())
        pattern_id = uuid.uuid4().hex
        post_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO recurring_patterns(
                    id,
                    user_id,
                    chat_id,
                    interval_type,
                    weekdays_mask,
                    time_of_day_minutes,
                    timezone,
                    start_at_utc,
                    end_at_utc,
                    max_occurrences,
                    current_count,
                    is_active,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    pattern_id,
                    user_id,
                    chat_id,
                    interval_type,
                    weekdays_mask,
                    time_of_day_minutes,
                    timezone,
                    start_at_utc,
                    end_at_utc,
                    max_occurrences,
                    now,
                    now,
                ),
            )

            await self._conn.execute(
                """
                INSERT INTO scheduled_posts(
                    id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                    caption, caption_entities_json, caption_above,
                    attempts, next_retry_at_utc, created_at
                ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, NULL, ?)
                """,
                (
                    post_id,
                    user_id,
                    chat_id,
                    start_at_utc,
                    kind,
                    text,
                    entities_json,
                    caption,
                    caption_entities_json,
                    None if caption_above is None else int(caption_above),
                    now,
                ),
            )
            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (post_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.execute(
                """
                INSERT INTO recurring_instances(pattern_id, post_id, ordinal, scheduled_for_utc, created_at)
                VALUES(?, ?, 1, ?, ?)
                """,
                (pattern_id, post_id, start_at_utc, now),
            )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return pattern_id, post_id

    async def get_recurring_instance_by_post_id(self, post_id: str) -> "RecurringInstance | None":
        row = await self._execute_fetchone(
            "SELECT * FROM recurring_instances WHERE post_id=?",
            (post_id,),
        )
        return None if row is None else self._row_to_recurring_instance(row)

    async def get_due_recurring_instances(self, now_utc: int, limit: int = 10) -> list["RecurringInstance"]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT ri.*
            FROM recurring_instances ri
            JOIN recurring_patterns rp ON rp.id = ri.pattern_id
            JOIN scheduled_posts sp ON sp.id = ri.post_id
            WHERE rp.is_active=1
              AND ri.scheduled_for_utc <= ?
              AND sp.status='pending'
              AND sp.scheduled_at_utc <= ?
              AND (sp.next_retry_at_utc IS NULL OR sp.next_retry_at_utc <= ?)
            ORDER BY ri.scheduled_for_utc ASC, ri.ordinal ASC
            LIMIT ?
            """,
            (now_utc, now_utc, now_utc, limit),
        )
        return [self._row_to_recurring_instance(row) for row in rows]
