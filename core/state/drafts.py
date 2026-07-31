from __future__ import annotations

import time
import uuid

from core.rbac import DraftPermissions, can_create_team_draft, resolve_draft_permissions
from core.state.models import DraftRow


class DraftsMixin:
    @staticmethod
    def _normalize_draft_payload(
        *,
        kind: str,
        text: str | None,
        entities_json: str | None,
        caption: str | None,
        caption_entities_json: str | None,
        caption_above: bool | None,
        media_items: list[dict[str, str]] | None,
    ) -> tuple[str | None, str | None, str | None, str | None, int | None, list[dict[str, str]]]:
        if kind not in {"text", "media"}:
            raise ValueError(f"Unsupported draft kind: {kind}")

        items = list(media_items or [])
        if kind == "text":
            if not str(text or "").strip():
                raise ValueError("Text draft must contain text")
            if items:
                raise ValueError("Text draft cannot contain media items")
            if caption is not None or caption_entities_json is not None or caption_above is not None:
                raise ValueError("Text draft cannot contain media caption fields")
            return text, entities_json, None, None, None, []

        if items == []:
            raise ValueError("Media draft must contain at least one media item")
        if text is not None or entities_json is not None:
            raise ValueError("Media draft must use caption fields instead of text fields")
        return None, None, caption, caption_entities_json, None if caption_above is None else int(caption_above), items

    async def _get_draft_access(self, draft_id: str, user_id: int) -> tuple["DraftRow | None", str | None]:
        draft = await self.get_draft(draft_id)
        if draft is None or draft.team_id is None:
            return draft, None
        return draft, await self.get_team_member_role(draft.team_id, user_id)

    async def create_draft(
        self,
        *,
        author_user_id: int,
        chat_id: int,
        kind: str,
        team_id: str | None = None,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> str:
        if team_id is not None:
            team_role = await self.get_team_member_role(team_id, author_user_id)
            if not can_create_team_draft(team_role):
                raise ValueError("User must be an owner or editor to write team drafts")

        normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
            self._normalize_draft_payload(
                kind=kind,
                text=text,
                entities_json=entities_json,
                caption=caption,
                caption_entities_json=caption_entities_json,
                caption_above=caption_above,
                media_items=media_items,
            )
        )

        now = int(time.time())
        draft_id = uuid.uuid4().hex

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.execute(
                """
                INSERT INTO drafts(
                    id,
                    team_id,
                    author_user_id,
                    chat_id,
                    kind,
                    text,
                    entities_json,
                    caption,
                    caption_entities_json,
                    caption_above,
                    created_at,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    team_id,
                    author_user_id,
                    chat_id,
                    kind,
                    normalized_text,
                    normalized_entities,
                    normalized_caption,
                    normalized_caption_entities,
                    normalized_caption_above,
                    now,
                    now,
                ),
            )

            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO draft_media(draft_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (draft_id, idx, item["type"], item["file_id"]),
                )

            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return draft_id

    async def get_draft(self, draft_id: str) -> "DraftRow | None":
        row = await self._execute_fetchone(
            "SELECT * FROM drafts WHERE id=?",
            (draft_id,),
        )
        return None if row is None else self._row_to_draft(row)

    async def get_draft_media(self, draft_id: str) -> list[dict[str, str]]:
        rows = await self._conn.execute_fetchall(
            """
            SELECT idx, type, file_id
            FROM draft_media
            WHERE draft_id=?
            ORDER BY idx ASC
            """,
            (draft_id,),
        )
        return [{"type": str(row["type"]), "file_id": str(row["file_id"])} for row in rows]

    async def get_draft_permissions(self, draft_id: str, user_id: int) -> DraftPermissions | None:
        draft, team_role = await self._get_draft_access(draft_id, user_id)
        if draft is None:
            return None
        return resolve_draft_permissions(
            draft_author_user_id=draft.author_user_id,
            acting_user_id=user_id,
            team_id=draft.team_id,
            team_role=team_role,
        )

    async def list_drafts(
        self,
        user_id: int,
        *,
        scope: str = "all",
        team_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list["DraftRow"]:
        if scope not in {"all", "mine", "team"}:
            raise ValueError(f"Unsupported draft scope: {scope}")

        query = """
            SELECT d.*
            FROM drafts d
            WHERE
        """
        params: list[object] = []

        if team_id is not None:
            query += " d.team_id=?"
            params.append(team_id)
            if scope == "mine":
                query += """
                    AND d.author_user_id=?
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                """
                params.extend((user_id, user_id))
            else:
                query += """
                    AND EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                """
                params.append(user_id)
        elif scope == "all":
            query += """
                (
                    (d.team_id IS NULL AND d.author_user_id=?)
                    OR EXISTS (
                        SELECT 1
                        FROM team_members tm
                        WHERE tm.team_id = d.team_id
                          AND tm.user_id = ?
                    )
                )
            """
            params.extend((user_id, user_id))
        elif scope == "mine":
            query += """
                (
                    (d.team_id IS NULL AND d.author_user_id=?)
                    OR (
                        d.author_user_id=?
                        AND EXISTS (
                            SELECT 1
                            FROM team_members tm
                            WHERE tm.team_id = d.team_id
                              AND tm.user_id = ?
                        )
                    )
                )
            """
            params.extend((user_id, user_id, user_id))
        else:
            query += """
                d.team_id IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM team_members tm
                    WHERE tm.team_id = d.team_id
                      AND tm.user_id = ?
                )
            """
            params.append(user_id)

        query += " ORDER BY d.updated_at DESC, d.created_at DESC, d.id ASC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        rows = await self._conn.execute_fetchall(query, tuple(params))
        return [self._row_to_draft(row) for row in rows]

    async def update_draft(
        self,
        draft_id: str,
        user_id: int,
        *,
        chat_id: int,
        kind: str,
        text: str | None = None,
        entities_json: str | None = None,
        caption: str | None = None,
        caption_entities_json: str | None = None,
        caption_above: bool | None = None,
        media_items: list[dict[str, str]] | None = None,
    ) -> bool:
        permissions = await self.get_draft_permissions(draft_id, user_id)
        if permissions is None or not permissions.can_edit:
            return False

        normalized_text, normalized_entities, normalized_caption, normalized_caption_entities, normalized_caption_above, items = (
            self._normalize_draft_payload(
                kind=kind,
                text=text,
                entities_json=entities_json,
                caption=caption,
                caption_entities_json=caption_entities_json,
                caption_above=caption_above,
                media_items=media_items,
            )
        )
        now = int(time.time())

        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self._conn.execute(
                """
                UPDATE drafts
                SET chat_id=?,
                    kind=?,
                    text=?,
                    entities_json=?,
                    caption=?,
                    caption_entities_json=?,
                    caption_above=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    chat_id,
                    kind,
                    normalized_text,
                    normalized_entities,
                    normalized_caption,
                    normalized_caption_entities,
                    normalized_caption_above,
                    now,
                    draft_id,
                ),
            )
            if cur.rowcount != 1:
                await self._conn.rollback()
                return False

            await self._conn.execute("DELETE FROM draft_media WHERE draft_id=?", (draft_id,))
            for idx, item in enumerate(items):
                await self._conn.execute(
                    "INSERT INTO draft_media(draft_id, idx, type, file_id) VALUES(?, ?, ?, ?)",
                    (draft_id, idx, item["type"], item["file_id"]),
                )
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

        return True

    async def delete_draft(self, draft_id: str, user_id: int) -> bool:
        permissions = await self.get_draft_permissions(draft_id, user_id)
        if permissions is None or not permissions.can_delete:
            return False

        cur = await self._conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
        await self._conn.commit()
        return cur.rowcount == 1
