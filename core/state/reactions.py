from __future__ import annotations

import time

from core.state.base import locked_write


class ReactionPresetsMixin:
    """Per-user reaction presets: ``(user_id, post_type) -> emojis_json``.

    A preset is a reusable emoji set the user labels by ``post_type`` (freeform,
    e.g. "весёлый"). The mini-app resolves a preset to a plain emoji list before
    creating a post; storage here holds no plan logic (validated at save time by
    the web layer against ``core.reactions``).
    """

    @locked_write
    async def upsert_reaction_preset(self, user_id: int, post_type: str, emojis_json: str) -> None:
        now = int(time.time())
        await self._conn.execute(
            """
            INSERT INTO reaction_presets(user_id, post_type, emojis_json, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, post_type) DO UPDATE SET
                emojis_json = excluded.emojis_json,
                updated_at  = excluded.updated_at
            """,
            (user_id, post_type, emojis_json, now),
        )
        await self._conn.commit()

    async def get_reaction_preset(self, user_id: int, post_type: str) -> str | None:
        row = await self._execute_fetchone(
            "SELECT emojis_json FROM reaction_presets WHERE user_id=? AND post_type=?",
            (user_id, post_type),
        )
        return None if row is None else str(row["emojis_json"])

    async def list_reaction_presets(self, user_id: int) -> list[dict[str, str]]:
        rows = await self._conn.execute_fetchall(
            "SELECT post_type, emojis_json FROM reaction_presets WHERE user_id=? ORDER BY post_type ASC",
            (user_id,),
        )
        return [{"post_type": str(r["post_type"]), "emojis_json": str(r["emojis_json"])} for r in rows]

    @locked_write
    async def delete_reaction_preset(self, user_id: int, post_type: str) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM reaction_presets WHERE user_id=? AND post_type=?",
            (user_id, post_type),
        )
        await self._conn.commit()
        return cur.rowcount == 1
