from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore

USER_ID = 1001
CHAT_IDS = [-2001, -2002]


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    await state.ensure_user(USER_ID)
    for index, chat_id in enumerate(CHAT_IDS, start=1):
        await state.upsert_destination(
            chat_id,
            "channel",
            f"Channel {index}",
            f"channel_{index}",
            "administrator",
            True,
        )
    yield state
    await conn.close()


@pytest.mark.asyncio
async def test_create_broadcast_text_posts_creates_one_post_per_chat(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())

    post_ids = await store.create_broadcast_posts(
        user_id=USER_ID,
        chat_ids=[CHAT_IDS[0], CHAT_IDS[1], CHAT_IDS[0]],
        scheduled_at_utc=scheduled_at_utc,
        kind="text",
        text="Broadcast text",
        entities_json=None,
    )

    assert len(post_ids) == 2
    assert len(set(post_ids)) == 2
    pending_posts = await store.list_pending_posts(USER_ID, limit=10)
    assert len(pending_posts) == 2
    assert {post.chat_id for post in pending_posts} == set(CHAT_IDS)
    assert {post.kind for post in pending_posts} == {"text"}
    assert {post.text for post in pending_posts} == {"Broadcast text"}


@pytest.mark.asyncio
async def test_create_broadcast_media_posts_copies_media_to_each_post(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    media_items = [
        {"type": "photo", "file_id": "photo-1"},
        {"type": "video", "file_id": "video-1"},
    ]

    post_ids = await store.create_broadcast_posts(
        user_id=USER_ID,
        chat_ids=CHAT_IDS,
        scheduled_at_utc=scheduled_at_utc,
        kind="media",
        caption="Media caption",
        caption_entities_json=None,
        caption_above=True,
        media_items=media_items,
    )

    assert len(post_ids) == 2
    pending_posts = await store.list_pending_posts(USER_ID, limit=10)
    assert len(pending_posts) == 2
    for post in pending_posts:
        assert post.kind == "media"
        assert post.caption == "Media caption"
        assert await store.get_post_media(post.id) == media_items


@pytest.mark.asyncio
async def test_create_broadcast_posts_rolls_back_on_media_error(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())

    with pytest.raises(KeyError):
        await store.create_broadcast_posts(
            user_id=USER_ID,
            chat_ids=CHAT_IDS,
            scheduled_at_utc=scheduled_at_utc,
            kind="media",
            caption="Broken media",
            caption_entities_json=None,
            caption_above=False,
            media_items=[
                {"type": "photo", "file_id": "photo-1"},
                {"type": "photo"},
            ],
        )

    assert await store.list_pending_posts(USER_ID, limit=10) == []
