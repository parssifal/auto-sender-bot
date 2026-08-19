from __future__ import annotations

import time

import pytest
import pytest_asyncio

import core.limits as limits
from core.db import open_db
from core.limits import ResourceLimitError
from core.state import StateStore

USER = 555
CHAT = -100123


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    await state.ensure_user(USER)
    await state.upsert_destination(CHAT, "channel", "Ch", None, "administrator", True)
    await state.link_user_destination(USER, CHAT, "seed")
    yield state
    await conn.close()


def _future() -> int:
    return int(time.time()) + 3600


@pytest.mark.asyncio
async def test_active_posts_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 2)
    await store.create_scheduled_text_post(USER, CHAT, _future(), "a", None)
    await store.create_scheduled_text_post(USER, CHAT, _future(), "b", None)
    with pytest.raises(ResourceLimitError) as exc:
        await store.create_scheduled_text_post(USER, CHAT, _future(), "c", None)
    assert exc.value.resource == "posts"
    assert exc.value.limit == 2


@pytest.mark.asyncio
async def test_sent_posts_do_not_count_toward_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 1)
    pid = await store.create_scheduled_text_post(USER, CHAT, _future(), "a", None)
    # Move it out of the active set; a new one must then be allowed.
    await store.claim_post_for_sending(post_id=pid, now_utc=int(time.time()))
    await store.mark_sent(post_id=pid, sent_at_utc=int(time.time()))
    await store.create_scheduled_text_post(USER, CHAT, _future(), "b", None)  # no raise


@pytest.mark.asyncio
async def test_media_posts_respect_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 1)
    await store.create_scheduled_text_post(USER, CHAT, _future(), "a", None)
    with pytest.raises(ResourceLimitError):
        await store.create_scheduled_media_post(
            USER, CHAT, _future(), None, None, None,
            [{"type": "photo", "file_id": "f"}],
        )


@pytest.mark.asyncio
async def test_drafts_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_DRAFTS_PER_USER", 1)
    await store.create_draft(author_user_id=USER, chat_id=CHAT, kind="text", text="a")
    with pytest.raises(ResourceLimitError) as exc:
        await store.create_draft(author_user_id=USER, chat_id=CHAT, kind="text", text="b")
    assert exc.value.resource == "drafts"


@pytest.mark.asyncio
async def test_destinations_cap_blocks_new_links(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_DESTINATIONS_PER_USER", 1)
    # USER already has 1 linked destination (seed) == the cap.
    other = -100999
    await store.upsert_destination(other, "channel", "Ch2", None, "administrator", True)
    with pytest.raises(ResourceLimitError) as exc:
        await store.link_user_destination(USER, other, "new")
    assert exc.value.resource == "destinations"


@pytest.mark.asyncio
async def test_relinking_existing_destination_is_allowed_at_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_DESTINATIONS_PER_USER", 1)
    # Re-linking the SAME destination is an upsert, not growth — must not raise.
    await store.link_user_destination(USER, CHAT, "relink")  # no raise


@pytest.mark.asyncio
async def test_broadcast_respects_active_posts_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 2)
    other = -100888
    await store.upsert_destination(other, "channel", "Ch2", None, "administrator", True)
    await store.link_user_destination(USER, other, "seed2")
    # Fanning out to 3 destinations at once exceeds the cap of 2.
    with pytest.raises(ResourceLimitError) as exc:
        await store.create_broadcast_posts(
            user_id=USER, chat_ids=[CHAT, other, -100777],
            scheduled_at_utc=_future(), kind="text", text="hi",
        )
    assert exc.value.resource == "posts"


@pytest.mark.asyncio
async def test_recurring_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_RECURRING_PER_USER", 1)
    await store.create_recurring_series(
        user_id=USER, chat_id=CHAT, interval_type="daily",
        time_of_day_minutes=600, timezone="UTC", start_at_utc=_future(),
        kind="text", text="a",
    )
    with pytest.raises(ResourceLimitError) as exc:
        await store.create_recurring_series(
            user_id=USER, chat_id=CHAT, interval_type="daily",
            time_of_day_minutes=600, timezone="UTC", start_at_utc=_future(),
            kind="text", text="b",
        )
    assert exc.value.resource == "recurring"

@pytest.mark.asyncio
async def test_recurring_series_counts_against_active_posts_cap(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 2)
    await store.create_scheduled_text_post(USER, CHAT, _future(), "a", None)
    await store.create_scheduled_text_post(USER, CHAT, _future(), "b", None)
    with pytest.raises(ResourceLimitError) as exc:
        await store.create_recurring_series(
            user_id=USER, chat_id=CHAT, interval_type="daily",
            time_of_day_minutes=600, timezone="UTC", start_at_utc=_future(),
            kind="text", text="series",
        )
    assert exc.value.resource == "posts"


# --- Per-plan limits (Phase 1): no monkeypatch, real plan tiers ---


@pytest.mark.asyncio
async def test_basic_plan_destinations_cap_is_five(store: StateStore) -> None:
    # basic tier allows 5 linked destinations; USER already has 1 (seed).
    for i in range(4):
        cid = -100000 - i
        await store.upsert_destination(cid, "channel", f"c{i}", None, "administrator", True)
        await store.link_user_destination(USER, cid, "seed")
    assert await store.count_user_destinations(USER) == 5
    over = -200000
    await store.upsert_destination(over, "channel", "over", None, "administrator", True)
    with pytest.raises(ResourceLimitError) as exc:
        await store.link_user_destination(USER, over, "new")
    assert exc.value.resource == "destinations"
    assert exc.value.limit == 5


@pytest.mark.asyncio
async def test_pro_plan_lifts_destinations_cap(store: StateStore) -> None:
    # pro tier allows 20 destinations, so the 6th link that basic rejects passes.
    await store.set_user_plan(USER, "pro", None)
    for i in range(10):  # total 11 with the seed — under pro's 20, no raise
        cid = -300000 - i
        await store.upsert_destination(cid, "channel", f"p{i}", None, "administrator", True)
        await store.link_user_destination(USER, cid, "seed")
    assert await store.count_user_destinations(USER) == 11


@pytest.mark.asyncio
async def test_materialization_respects_active_posts_cap(store: StateStore, monkeypatch) -> None:
    pattern_id, post_id = await store.create_recurring_series(
        user_id=USER, chat_id=CHAT, interval_type="daily",
        time_of_day_minutes=600, timezone="UTC", start_at_utc=_future(),
        kind="text", text="series",
    )
    await store.create_scheduled_text_post(USER, CHAT, _future(), "a", None)
    monkeypatch.setattr(limits, "MAX_ACTIVE_POSTS_PER_USER", 2)
    with pytest.raises(ResourceLimitError) as exc:
        await store.materialize_next_recurring_post(
            pattern_id=pattern_id,
            source_post_id=post_id,
            next_ordinal=2,
            scheduled_for_utc=_future() + 86400,
        )
    assert exc.value.resource == "posts"
