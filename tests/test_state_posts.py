from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore

USER_ID = 1001
CHAT_ID = -2001


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
async def test_list_editable_pending_posts_excludes_recurring(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    editable_post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )
    _, recurring_post_id = await store.create_recurring_series(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc + 3600,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    editable_posts = await store.list_editable_pending_posts(USER_ID, limit=10)

    assert [post.id for post in editable_posts] == [editable_post_id]
    assert recurring_post_id not in {post.id for post in editable_posts}


@pytest.mark.asyncio
async def test_update_editable_post_time_updates_pending_non_recurring_post(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    updated_at_utc = int(datetime(2099, 12, 31, 7, 45, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )

    updated = await store.update_editable_post_time(post_id, USER_ID, scheduled_at_utc=updated_at_utc)

    post = await store.get_scheduled_post(post_id)
    assert updated is True
    assert post is not None
    assert post.scheduled_at_utc == updated_at_utc


@pytest.mark.asyncio
async def test_update_editable_post_content_rolls_back_media_replacement_on_error(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    original_media = [
        {"type": "photo", "file_id": "photo-1"},
        {"type": "video", "file_id": "video-1"},
    ]
    post_id = await store.create_scheduled_media_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        caption="Original caption",
        caption_entities_json=None,
        caption_above=True,
        media_items=original_media,
    )

    with pytest.raises(KeyError):
        await store.update_editable_post_content(
            post_id,
            USER_ID,
            kind="media",
            caption="Broken replacement",
            caption_entities_json=None,
            caption_above=True,
            media_items=[
                {"type": "photo", "file_id": "photo-2"},
                {"type": "photo"},
            ],
        )

    post = await store.get_scheduled_post(post_id)
    assert post is not None
    assert post.caption == "Original caption"
    assert await store.get_post_media(post_id) == original_media


@pytest.mark.asyncio
async def test_update_editable_post_content_rejects_recurring_post(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    _, post_id = await store.create_recurring_series(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    updated = await store.update_editable_post_content(
        post_id,
        USER_ID,
        kind="text",
        text="Updated text",
        entities_json=None,
    )

    post = await store.get_scheduled_post(post_id)
    assert updated is False
    assert post is not None
    assert post.text == "Recurring post"
