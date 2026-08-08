from __future__ import annotations

import asyncio
import sqlite3

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore

USER_ID = 7001
CHAT_ID = -7001
BASE_TS = 4_000_000_000


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    await state.ensure_user(USER_ID)
    await state.upsert_destination(
        CHAT_ID,
        "channel",
        "Test channel",
        "test_channel",
        "administrator",
        True,
    )
    yield state
    await conn.close()


@pytest.mark.asyncio
async def test_concurrent_writers_never_collide(store: StateStore) -> None:
    """Four concurrent writers on one connection must not trip over each other.

    Mixes explicit-BEGIN methods (update_scheduled_post, cancel_recurring_pattern)
    with implicit-transaction + commit methods (create_scheduled_text_post,
    ensure_user); without serialization the explicit BEGIN lands inside another
    method's open implicit transaction.
    """
    post_ids = [
        await store.create_scheduled_text_post(
            user_id=USER_ID,
            chat_id=CHAT_ID,
            scheduled_at_utc=BASE_TS,
            text=f"seed {i}",
            entities_json=None,
        )
        for i in range(2)
    ]
    pattern_ids = []
    for i in range(20):
        pattern_id, _ = await store.create_recurring_series(
            user_id=USER_ID,
            chat_id=CHAT_ID,
            interval_type="daily",
            time_of_day_minutes=9 * 60,
            timezone="Europe/Moscow",
            start_at_utc=BASE_TS + i,
            kind="text",
            text=f"recurring {i}",
            entities_json=None,
        )
        pattern_ids.append(pattern_id)

    errors: list[sqlite3.OperationalError] = []

    async def creator(tag: str) -> None:
        for i in range(20):
            try:
                await store.create_scheduled_text_post(
                    user_id=USER_ID,
                    chat_id=CHAT_ID,
                    scheduled_at_utc=BASE_TS + i,
                    text=f"{tag}-{i}",
                    entities_json=None,
                )
            except sqlite3.OperationalError as exc:
                errors.append(exc)

    async def updater(post_id: str) -> None:
        for i in range(20):
            try:
                await store.update_scheduled_post(post_id, USER_ID, {"scheduled_at_utc": BASE_TS + i})
            except sqlite3.OperationalError as exc:
                errors.append(exc)

    async def canceller() -> None:
        for pattern_id in pattern_ids:
            try:
                await store.cancel_recurring_pattern(USER_ID, pattern_id)
            except sqlite3.OperationalError as exc:
                errors.append(exc)

    async def toucher() -> None:
        for _ in range(20):
            try:
                await store.ensure_user(USER_ID)
            except sqlite3.OperationalError as exc:
                errors.append(exc)

    await asyncio.gather(
        creator("a"),
        creator("b"),
        updater(post_ids[0]),
        canceller(),
        toucher(),
    )

    assert not errors, f"{len(errors)} OperationalError(s): {sorted({str(e) for e in errors})}"


@pytest.mark.asyncio
async def test_rollback_survives_concurrent_commits(store: StateStore) -> None:
    """A rolled-back write must leave nothing behind, even while others commit.

    ``update_scheduled_post`` opens a transaction, rewrites the post, drops its
    media rows, then dies on a malformed media item and rolls back. A ``commit()``
    issued by another coroutine on the shared connection commits that half-applied
    update first, so the rollback undoes nothing and the post loses its media.
    """
    post_id = await store.create_scheduled_media_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=BASE_TS,
        caption="original",
        caption_entities_json=None,
        caption_above=None,
        media_items=[{"type": "photo", "file_id": "m1"}, {"type": "photo", "file_id": "m2"}],
    )
    before_post = await store.get_scheduled_post(post_id)
    before_media = await store.get_post_media(post_id)

    async def failing_updates() -> None:
        for _ in range(30):
            with pytest.raises((KeyError, sqlite3.OperationalError)):
                await store.update_scheduled_post(
                    post_id,
                    USER_ID,
                    {
                        "kind": "media",
                        "caption": "LEAKED",
                        "media_items": [
                            {"type": "photo", "file_id": "x"},
                            {"type": "photo"},  # missing file_id -> KeyError mid-transaction
                        ],
                    },
                )

    async def committer() -> None:
        for _ in range(60):
            await store.ensure_user(USER_ID)

    await asyncio.gather(failing_updates(), committer(), committer())

    after_post = await store.get_scheduled_post(post_id)
    assert after_post.caption == before_post.caption
    assert await store.get_post_media(post_id) == before_media
