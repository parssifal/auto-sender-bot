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
async def test_create_media_post_rolls_back_on_media_failure(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    # Second item is malformed (missing "file_id") -> KeyError on the 2nd media
    # insert, after the post row and first media row are already written.
    media_items = [
        {"type": "photo", "file_id": "abc"},
        {"type": "photo"},
    ]

    with pytest.raises(KeyError):
        await store.create_scheduled_media_post(
            user_id=USER_ID,
            chat_id=CHAT_ID,
            scheduled_at_utc=scheduled_at_utc,
            caption=None,
            caption_entities_json=None,
            caption_above=None,
            media_items=media_items,
        )

    async with store._conn.execute("SELECT COUNT(*) FROM scheduled_posts") as cur:
        (post_count,) = await cur.fetchone()
    async with store._conn.execute("SELECT COUNT(*) FROM scheduled_post_media") as cur:
        (media_count,) = await cur.fetchone()
    assert post_count == 0, "orphan post row left after failed media insert"
    assert media_count == 0


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
async def test_update_scheduled_post_updates_pending_non_recurring_post(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    updated_at_utc = int(datetime(2099, 12, 31, 7, 45, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )

    updated = await store.update_scheduled_post(
        post_id,
        USER_ID,
        {"scheduled_at_utc": updated_at_utc},
    )

    post = await store.get_scheduled_post(post_id)
    assert updated is True
    assert post is not None
    assert post.scheduled_at_utc == updated_at_utc


@pytest.mark.asyncio
async def test_update_scheduled_post_moves_post_to_another_channel(store: StateStore) -> None:
    """Retargeting is content-independent: it must work on the time-only path too."""
    other_chat_id = -2002
    await store.upsert_destination(other_chat_id, "channel", "Second", None, "administrator", True)
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )

    moved = await store.update_scheduled_post(post_id, USER_ID, {"chat_id": other_chat_id})

    post = await store.get_scheduled_post(post_id)
    assert moved is True
    assert post is not None
    assert post.chat_id == other_chat_id
    # Untouched fields survive a move.
    assert post.scheduled_at_utc == scheduled_at_utc
    assert post.text == "Editable post"


@pytest.mark.asyncio
async def test_update_scheduled_post_moves_channel_together_with_content(store: StateStore) -> None:
    other_chat_id = -2003
    await store.upsert_destination(other_chat_id, "channel", "Second", None, "administrator", True)
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Before",
        entities_json=None,
    )

    updated = await store.update_scheduled_post(
        post_id,
        USER_ID,
        {"chat_id": other_chat_id, "kind": "text", "text": "After", "entities_json": None},
    )

    post = await store.get_scheduled_post(post_id)
    assert updated is True
    assert post is not None
    assert (post.chat_id, post.text) == (other_chat_id, "After")


@pytest.mark.asyncio
async def test_update_scheduled_post_rolls_back_media_replacement_on_error(store: StateStore) -> None:
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
        await store.update_scheduled_post(
            post_id,
            USER_ID,
            {
                "kind": "media",
                "caption": "Broken replacement",
                "caption_entities_json": None,
                "caption_above": True,
                "media_items": [
                    {"type": "photo", "file_id": "photo-2"},
                    {"type": "photo"},
                ],
            },
        )

    post = await store.get_scheduled_post(post_id)
    assert post is not None
    assert post.caption == "Original caption"
    assert await store.get_post_media(post_id) == original_media


@pytest.mark.asyncio
async def test_update_scheduled_post_rejects_recurring_post(store: StateStore) -> None:
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

    updated = await store.update_scheduled_post(
        post_id,
        USER_ID,
        {
            "kind": "text",
            "text": "Updated text",
            "entities_json": None,
        },
    )

    post = await store.get_scheduled_post(post_id)
    assert updated is False
    assert post is not None
    assert post.text == "Recurring post"


@pytest.mark.asyncio
async def test_update_scheduled_post_rejects_unsupported_field(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )

    with pytest.raises(ValueError):
        await store.update_scheduled_post(post_id, USER_ID, {"status": "sent"})


@pytest.mark.asyncio
async def test_hard_delete_post_removes_post_and_media(store: StateStore) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_media_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        caption="Caption",
        caption_entities_json=None,
        caption_above=False,
        media_items=[{"type": "photo", "file_id": "photo-1"}],
    )

    deleted = await store.hard_delete_post(USER_ID, post_id)

    assert deleted is True
    assert await store.get_scheduled_post(post_id) is None
    assert await store.get_post_media(post_id) == []


@pytest.mark.asyncio
async def test_hard_delete_post_rejects_recurring_post(store: StateStore) -> None:
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

    deleted = await store.hard_delete_post(USER_ID, post_id)

    post = await store.get_scheduled_post(post_id)
    assert deleted is False
    assert post is not None


@pytest.mark.asyncio
async def test_cancel_post_rejects_recurring_instance(store: StateStore) -> None:
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

    cancelled = await store.cancel_post(USER_ID, post_id)

    post = await store.get_scheduled_post(post_id)
    assert cancelled is False
    assert post is not None and post.status == "pending"


@pytest.mark.asyncio
async def test_startup_requeues_stuck_sending_post(store: StateStore) -> None:
    """A crash between claim and mark_* leaves 'sending' forever: invisible to
    list_due_posts, un-cancellable, and holding a quota slot."""
    scheduled_at_utc = int(datetime(2020, 1, 1, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Stuck post",
        entities_json=None,
    )
    now_utc = scheduled_at_utc + 60
    assert await store.claim_post_for_sending(post_id=post_id, now_utc=now_utc) is True

    # While stuck in 'sending': invisible to the scheduler yet still holding a quota slot.
    assert post_id not in {p.id for p in await store.list_due_posts(now_utc=now_utc)}
    assert await store.count_active_posts(USER_ID) == 1

    # Process restarts: same start path main.py runs.
    await store.migrate()

    post = await store.get_scheduled_post(post_id)
    assert post is not None and post.status == "pending"
    assert post.attempts == 1
    # Consequence 1: visible to the scheduler again.
    assert post_id in {p.id for p in await store.list_due_posts(now_utc=now_utc)}
    # Consequence 2: the slot is releasable again - cancel frees it (impossible while
    # stuck in 'sending', which cancel_post refuses). Both were held hostage forever.
    assert await store.count_active_posts(USER_ID) == 1
    assert await store.cancel_post(USER_ID, post_id) is True
    assert await store.count_active_posts(USER_ID) == 0
