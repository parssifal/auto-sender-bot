from __future__ import annotations

import pytest

from core.db import open_db
from core.state import StateStore


async def _plan(conn, query, params):
    rows = await conn.execute_fetchall("EXPLAIN QUERY PLAN " + query, params)
    return " ".join(str(r[-1]) for r in rows)


@pytest.mark.asyncio
async def test_queue_and_count_use_per_user_index() -> None:
    # Without idx_scheduled_user_status_time these queries fall back to the
    # status-first idx_scheduled_due and scan every pending post in the DB.
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    try:
        queue = await _plan(
            conn,
            "SELECT * FROM scheduled_posts WHERE user_id=? AND status='pending' "
            "ORDER BY scheduled_at_utc ASC LIMIT ? OFFSET ?",
            (1, 10, 0),
        )
        assert "idx_scheduled_user_status_time" in queue
        assert "user_id=?" in queue
        assert "TEMP B-TREE" not in queue  # scheduled_at_utc order comes from the index

        count = await _plan(
            conn,
            "SELECT COUNT(1) FROM scheduled_posts WHERE user_id=? AND status IN ('pending','sending')",
            (1,),
        )
        assert "idx_scheduled_user_status_time" in count
        assert "SCAN scheduled_posts" not in count
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_drafts_list_uses_updated_index() -> None:
    # Without idx_drafts_author_updated the ORDER BY d.updated_at DESC builds a
    # full TEMP B-TREE over the author's drafts.
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    try:
        plan = await _plan(
            conn,
            "SELECT d.* FROM drafts d WHERE d.team_id IS NULL AND d.author_user_id=? "
            "ORDER BY d.updated_at DESC, d.created_at DESC, d.id ASC LIMIT ? OFFSET ?",
            (1, 10, 0),
        )
        assert "idx_drafts_author_updated" in plan
    finally:
        await conn.close()
