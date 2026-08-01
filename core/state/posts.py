from __future__ import annotations

import time
import uuid
from typing import Iterable

import core.limits as limits
from core.limits import ResourceLimitError
from core.state.models import ScheduledPostRow


class PostsMixin:
    async def count_active_posts(self, user_id: int) -> int:
        """Un-sent scheduled posts (status in pending/sending) for a user."""
        row = await self._execute_fetchone(
            "SELECT COUNT(1) AS cnt FROM scheduled_posts "
            "WHERE user_id=? AND status IN ('pending', 'sending')",
            (user_id,),
        )
        return 0 if row is None else int(row["cnt"])

    async def _guard_active_posts_cap(self, user_id: int) -> None:
        if await self.count_active_posts(user_id) >= limits.MAX_ACTIVE_POSTS_PER_USER:
            raise ResourceLimitError("posts", limits.MAX_ACTIVE_POSTS_PER_USER)

    async def create_scheduled_text_post(
        self,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        text: str,
        entities_json: str | None,
    ) -> str:
        await self._guard_active_posts_cap(user_id)
        post_id = uuid.uuid4().hex
        await self._insert_scheduled_text_post(
            post_id=post_id,
            user_id=user_id,
            chat_id=chat_id,
            scheduled_at_utc=scheduled_at_utc,
            text=text,
            entities_json=entities_json,
            created_at=int(time.time()),
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
        await self._guard_active_posts_cap(user_id)
        post_id = uuid.uuid4().hex
        await self._insert_scheduled_media_post(
            post_id=post_id,
            user_id=user_id,
            chat_id=chat_id,
            scheduled_at_utc=scheduled_at_utc,
            caption=caption,
            caption_entities_json=caption_entities_json,
            caption_above=caption_above,
            media_items=media_items,
            created_at=int(time.time()),
        )
        await self._conn.commit()
        return post_id

    async def create_broadcast_posts(
        self,
        *,
        user_id: int,
        chat_ids: Iterable[int],
        scheduled_at_utc: int,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> list[str]:
        unique_chat_ids = list(dict.fromkeys(chat_ids))
        if not unique_chat_ids:
            return []
        if kind not in {"text", "media"}:
            raise ValueError("kind must be 'text' or 'media'")
        if kind == "media" and not media_items:
            raise ValueError("media_items are required for media broadcast")

        # A broadcast fans out to one post per destination; count them against
        # the same active-posts cap so repeated broadcasts can't grow unbounded.
        active = await self.count_active_posts(user_id)
        if active + len(unique_chat_ids) > limits.MAX_ACTIVE_POSTS_PER_USER:
            raise ResourceLimitError("posts", limits.MAX_ACTIVE_POSTS_PER_USER)

        created_at = int(time.time())
        post_ids: list[str] = []
        try:
            for chat_id in unique_chat_ids:
                post_id = uuid.uuid4().hex
                if kind == "text":
                    await self._insert_scheduled_text_post(
                        post_id=post_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        scheduled_at_utc=scheduled_at_utc,
                        text=str(text or ""),
                        entities_json=entities_json,
                        created_at=created_at,
                    )
                else:
                    await self._insert_scheduled_media_post(
                        post_id=post_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        scheduled_at_utc=scheduled_at_utc,
                        caption=caption,
                        caption_entities_json=caption_entities_json,
                        caption_above=caption_above,
                        media_items=list(media_items or []),
                        created_at=created_at,
                    )
                post_ids.append(post_id)
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise
        return post_ids

    async def _insert_scheduled_text_post(
        self,
        *,
        post_id: str,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        text: str,
        entities_json: str | None,
        created_at: int,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO scheduled_posts(
                id, user_id, chat_id, scheduled_at_utc, status, kind, text, entities_json,
                attempts, next_retry_at_utc, created_at
            ) VALUES(?, ?, ?, ?, 'pending', 'text', ?, ?, 0, NULL, ?)
            """,
            (post_id, user_id, chat_id, scheduled_at_utc, text, entities_json, created_at),
        )

    async def _insert_scheduled_media_post(
        self,
        *,
        post_id: str,
        user_id: int,
        chat_id: int,
        scheduled_at_utc: int,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]],
        created_at: int,
    ) -> None:
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
                created_at,
            ),
        )
        for idx, item in enumerate(media_items):
            await self._conn.execute(
                "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                (post_id, idx, str(item["type"]), str(item["file_id"])),
            )

    def _normalize_scheduled_payload(
        self,
        *,
        kind: str,
        text: str | None,
        entities_json: str | None,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]] | None,
    ) -> tuple[str | None, str | None, str | None, str | None, int | None, list[dict[str, str]]]:
        return self._normalize_draft_payload(
            kind=kind,
            text=text,
            entities_json=entities_json,
            caption=caption,
            caption_entities_json=caption_entities_json,
            caption_above=caption_above,
            media_items=media_items,
        )

    @staticmethod
    def _editable_post_update_keys() -> set[str]:
        return {
            "scheduled_at_utc",
            "kind",
            "text",
            "entities_json",
            "caption",
            "caption_entities_json",
            "caption_above",
            "media_items",
        }

    async def list_pending_posts(self, user_id: int, limit: int = 10, offset: int = 0) -> list["ScheduledPostRow"]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT *
            FROM scheduled_posts
            WHERE user_id=? AND status='pending'
            ORDER BY scheduled_at_utc ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        return [self._row_to_post(r) for r in rows]

    async def list_editable_pending_posts(self, user_id: int, limit: int = 10, offset: int = 0) -> list["ScheduledPostRow"]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT sp.*
            FROM scheduled_posts sp
            LEFT JOIN recurring_instances ri ON ri.post_id = sp.id
            WHERE sp.user_id=?
              AND sp.status='pending'
              AND ri.post_id IS NULL
            ORDER BY sp.scheduled_at_utc ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        )
        return [self._row_to_post(r) for r in rows]

    async def get_scheduled_post(self, post_id: str) -> "ScheduledPostRow | None":
        row = await self._execute_fetchone(
            "SELECT * FROM scheduled_posts WHERE id=?",
            (post_id,),
        )
        return None if row is None else self._row_to_post(row)

    async def update_scheduled_post(self, post_id: str, user_id: int, updates: dict[str, object]) -> bool:
        if not updates:
            raise ValueError("updates must not be empty")
        unsupported_keys = set(updates) - self._editable_post_update_keys()
        if unsupported_keys:
            raise ValueError(f"Unsupported scheduled post updates: {sorted(unsupported_keys)}")

        content_keys = {
            "kind",
            "text",
            "entities_json",
            "caption",
            "caption_entities_json",
            "caption_above",
            "media_items",
        }
        content_update_requested = any(key in updates for key in content_keys)

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = await self._execute_fetchone(
                """
                SELECT sp.*
                FROM scheduled_posts sp
                LEFT JOIN recurring_instances ri ON ri.post_id = sp.id
                WHERE sp.id=?
                  AND user_id=?
                  AND status='pending'
                  AND ri.post_id IS NULL
                """,
                (post_id, user_id),
            )
            if row is None:
                await self._conn.rollback()
                return False
            current_post = self._row_to_post(row)

            next_scheduled_at_utc = int(updates.get("scheduled_at_utc", current_post.scheduled_at_utc))

            if content_update_requested:
                next_kind = str(updates.get("kind", current_post.kind))
                current_media_items = await self.get_post_media(post_id) if current_post.kind == "media" else []

                if next_kind == "text":
                    if any(key in updates for key in {"caption", "caption_entities_json", "caption_above", "media_items"}):
                        raise ValueError("Text scheduled post cannot include media fields")
                    normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
                        self._normalize_scheduled_payload(
                            kind="text",
                            text=(
                                str(updates["text"])
                                if "text" in updates and updates["text"] is not None
                                else current_post.text
                            ),
                            entities_json=(
                                None if "entities_json" in updates and updates["entities_json"] is None else updates.get("entities_json", current_post.entities_json)
                            ),
                            caption=None,
                            caption_entities_json=None,
                            caption_above=None,
                            media_items=None,
                        )
                    )
                elif next_kind == "media":
                    if any(key in updates for key in {"text", "entities_json"}):
                        raise ValueError("Media scheduled post must use caption fields instead of text fields")
                    normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
                        self._normalize_scheduled_payload(
                            kind="media",
                            text=None,
                            entities_json=None,
                            caption=(
                                None if "caption" in updates and updates["caption"] is None else updates.get("caption", current_post.caption)
                            ),
                            caption_entities_json=(
                                None
                                if "caption_entities_json" in updates and updates["caption_entities_json"] is None
                                else updates.get("caption_entities_json", current_post.caption_entities_json)
                            ),
                            caption_above=(
                                updates["caption_above"]
                                if "caption_above" in updates
                                else (None if current_post.caption_above is None else bool(current_post.caption_above))
                            ),
                            media_items=(
                                list(updates["media_items"])
                                if "media_items" in updates and updates["media_items"] is not None
                                else current_media_items
                            ),
                        )
                    )
                else:
                    raise ValueError(f"Unsupported scheduled post kind: {next_kind}")

                cur = await self._conn.execute(
                    """
                    UPDATE scheduled_posts
                    SET scheduled_at_utc=?,
                        kind=?,
                        text=?,
                        entities_json=?,
                        caption=?,
                        caption_entities_json=?,
                        caption_above=?
                    WHERE id=?
                    """,
                    (
                        next_scheduled_at_utc,
                        next_kind,
                        normalized_text,
                        normalized_entities,
                        normalized_caption,
                        normalized_caption_entities,
                        normalized_caption_above,
                        post_id,
                    ),
                )
                if cur.rowcount != 1:
                    await self._conn.rollback()
                    return False

                await self._conn.execute("DELETE FROM scheduled_post_media WHERE post_id=?", (post_id,))
                for idx, item in enumerate(items):
                    await self._conn.execute(
                        "INSERT INTO scheduled_post_media(post_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                        (post_id, idx, str(item["type"]), str(item["file_id"])),
                    )
            else:
                cur = await self._conn.execute(
                    """
                    UPDATE scheduled_posts
                    SET scheduled_at_utc=?
                    WHERE id=?
                    """,
                    (next_scheduled_at_utc, post_id),
                )
                if cur.rowcount != 1:
                    await self._conn.rollback()
                    return False

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def update_editable_post_time(self, post_id: str, user_id: int, *, scheduled_at_utc: int) -> bool:
        return await self.update_scheduled_post(
            post_id,
            user_id,
            {"scheduled_at_utc": scheduled_at_utc},
        )

    async def update_editable_post_content(
        self,
        post_id: str,
        user_id: int,
        *,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> bool:
        if kind == "text":
            updates: dict[str, object] = {
                "kind": kind,
                "text": text,
                "entities_json": entities_json,
            }
        elif kind == "media":
            updates = {
                "kind": kind,
                "caption": caption,
                "caption_entities_json": caption_entities_json,
                "caption_above": caption_above,
            }
            if media_items is not None:
                updates["media_items"] = media_items
        else:
            raise ValueError(f"Unsupported scheduled post kind: {kind}")
        return await self.update_scheduled_post(
            post_id,
            user_id,
            updates,
        )

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

    async def hard_delete_post(self, user_id: int, post_id: str) -> bool:
        cur = await self._conn.execute(
            """
            DELETE FROM scheduled_posts
            WHERE id=?
              AND user_id=?
              AND status='pending'
              AND NOT EXISTS (
                  SELECT 1
                  FROM recurring_instances
                  WHERE post_id=?
              )
            """,
            (post_id, user_id, post_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def hard_delete_pending_post(self, user_id: int, post_id: str) -> bool:
        return await self.hard_delete_post(user_id, post_id)

    async def list_due_posts(self, now_utc: int, limit: int = 10) -> list["ScheduledPostRow"]:
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
