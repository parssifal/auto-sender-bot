from __future__ import annotations

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore

NOW = 1_800_000_000  # fixed "current" epoch for deterministic windows
DAY = 86_400
CHAT_A = -2001
CHAT_B = -2002
CHAT_C = -2003


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


async def _set_user_created_at(store: StateStore, user_id: int, ts: int) -> None:
    await store._conn.execute("UPDATE users SET created_at=? WHERE user_id=?", (ts, user_id))
    await store._conn.commit()


async def _set_post_created_at(store: StateStore, post_id: str, ts: int) -> None:
    await store._conn.execute("UPDATE scheduled_posts SET created_at=? WHERE id=?", (ts, post_id))
    await store._conn.commit()


async def _seed_destinations(store: StateStore) -> None:
    for chat_id in (CHAT_A, CHAT_B, CHAT_C):
        await store.upsert_destination(chat_id, "channel", f"Ch {chat_id}", None, "administrator", True)


async def _mark_sent(store: StateStore, post_id: str, sent_at: int) -> None:
    # A post can only be marked sent after being claimed (pending -> sending -> sent).
    await store.claim_post_for_sending(post_id, sent_at)
    await store.mark_sent(post_id, sent_at)


# ---------------------------------------------------------------------------
# Headline counters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_users(store: StateStore) -> None:
    assert await store.count_users() == 0
    await store.ensure_user(1)
    await store.ensure_user(2)
    assert await store.count_users() == 2


@pytest.mark.asyncio
async def test_avg_destinations_per_user(store: StateStore) -> None:
    assert await store.avg_destinations_per_user() == 0.0
    await _seed_destinations(store)
    await store.ensure_user(1)
    await store.ensure_user(2)
    # user 1 -> 2 channels, user 2 -> 1 channel => avg 1.5
    await store.link_user_destination(1, CHAT_A, "link")
    await store.link_user_destination(1, CHAT_B, "link")
    await store.link_user_destination(2, CHAT_C, "link")
    assert await store.avg_destinations_per_user() == 1.5


@pytest.mark.asyncio
async def test_count_new_users_window(store: StateStore) -> None:
    await store.ensure_user(1)
    await store.ensure_user(2)
    await _set_user_created_at(store, 1, NOW - 2 * DAY)   # inside 7d
    await _set_user_created_at(store, 2, NOW - 20 * DAY)  # outside 7d, inside 30d
    assert await store.count_new_users(NOW - 7 * DAY) == 1
    assert await store.count_new_users(NOW - 30 * DAY) == 2


@pytest.mark.asyncio
async def test_count_active_users_window(store: StateStore) -> None:
    await _seed_destinations(store)
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.link_user_destination(1, CHAT_A, "link")
    await store.link_user_destination(2, CHAT_B, "link")
    p1 = await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW + DAY, text="a", entities_json=None)
    p2 = await store.create_scheduled_text_post(user_id=2, chat_id=CHAT_B, scheduled_at_utc=NOW + DAY, text="b", entities_json=None)
    await _set_post_created_at(store, p1, NOW - 1 * DAY)   # active in 7d
    await _set_post_created_at(store, p2, NOW - 15 * DAY)  # not active in 7d
    assert await store.count_active_users(NOW - 7 * DAY) == 1
    assert await store.count_active_users(NOW - 30 * DAY) == 2


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_posts_by_status(store: StateStore) -> None:
    await _seed_destinations(store)
    await store.ensure_user(1)
    await store.link_user_destination(1, CHAT_A, "link")
    pending = await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW + DAY, text="p", entities_json=None)
    to_send = await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW + DAY, text="s", entities_json=None)
    await _mark_sent(store, to_send, NOW - DAY)

    counts = await store.count_posts_by_status()
    assert counts["pending"] == 1
    assert counts["sent"] == 1
    # every known status key is present, defaulting to 0
    assert counts["failed"] == 0
    assert counts["cancelled"] == 0
    assert pending  # sanity


@pytest.mark.asyncio
async def test_count_posts_sent_since(store: StateStore) -> None:
    await _seed_destinations(store)
    await store.ensure_user(1)
    await store.link_user_destination(1, CHAT_A, "link")
    recent = await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW, text="r", entities_json=None)
    old = await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW, text="o", entities_json=None)
    await _mark_sent(store, recent, NOW - 2 * DAY)
    await _mark_sent(store, old, NOW - 10 * DAY)
    assert await store.count_posts_sent_since(NOW - 7 * DAY) == 1


# ---------------------------------------------------------------------------
# Distributions / misc
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_language_distribution(store: StateStore) -> None:
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.ensure_user(3)
    await store.set_user_language(1, "ru")
    await store.set_user_language(2, "ru")
    await store.set_user_language(3, "en")
    dist = dict(await store.language_distribution())
    assert dist["ru"] == 2
    assert dist["en"] == 1


@pytest.mark.asyncio
async def test_top_active_users(store: StateStore) -> None:
    await _seed_destinations(store)
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.link_user_destination(1, CHAT_A, "link")
    await store.link_user_destination(2, CHAT_B, "link")
    for _ in range(3):
        await store.create_scheduled_text_post(user_id=1, chat_id=CHAT_A, scheduled_at_utc=NOW + DAY, text="x", entities_json=None)
    await store.create_scheduled_text_post(user_id=2, chat_id=CHAT_B, scheduled_at_utc=NOW + DAY, text="y", entities_json=None)
    top = await store.top_active_users(limit=10, since_ts=0)
    assert top[0] == (1, 3)
    assert (2, 1) in top


@pytest.mark.asyncio
async def test_get_user_profile(store: StateStore) -> None:
    await _seed_destinations(store)
    await store.ensure_user(7)
    await store.set_user_language(7, "de")
    await store.set_user_timezone(7, "Europe/Berlin")
    await store.link_user_destination(7, CHAT_A, "link")
    await store.link_user_destination(7, CHAT_B, "link")
    await store.create_scheduled_text_post(user_id=7, chat_id=CHAT_A, scheduled_at_utc=NOW + DAY, text="z", entities_json=None)

    profile = await store.get_user_profile(7)
    assert profile is not None
    assert profile["user_id"] == 7
    assert profile["language"] == "de"
    assert profile["timezone"] == "Europe/Berlin"
    assert profile["channels"] == 2
    assert profile["posts"] == 1

    assert await store.get_user_profile(9999) is None


@pytest.mark.asyncio
async def test_daily_new_users_buckets(store: StateStore) -> None:
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.ensure_user(3)
    await _set_user_created_at(store, 1, NOW - 1 * DAY)
    await _set_user_created_at(store, 2, NOW - 1 * DAY)
    await _set_user_created_at(store, 3, NOW - 3 * DAY)
    buckets = dict(await store.daily_new_users(since_ts=NOW - 7 * DAY))
    # keyed by YYYY-MM-DD; two users share one day, one user another day
    assert sum(buckets.values()) == 3
    assert max(buckets.values()) == 2
