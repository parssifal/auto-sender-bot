from __future__ import annotations

import pytest

from core.state import StateStore


@pytest.mark.asyncio
async def test_ensure_user_captures_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice"
    assert prof["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_ensure_user_coalesce_preserves_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    # A later interaction with no name (e.g. a callback) must not wipe it.
    await store.ensure_user(1)
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice"
    assert prof["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_ensure_user_updates_name(store: StateStore) -> None:
    await store.ensure_user(1, username="alice", first_name="Alice")
    await store.ensure_user(1, username="alice2", first_name="Alicia")
    prof = await store.get_user_profile(1)
    assert prof["username"] == "alice2"
    assert prof["first_name"] == "Alicia"


@pytest.mark.asyncio
async def test_list_users_orders_by_last_active(store: StateStore) -> None:
    await store.ensure_user(1, username="a", first_name="A")
    await store.ensure_user(2, username="b", first_name="B")
    # Force distinct updated_at so ordering is deterministic.
    await store._conn.execute("UPDATE users SET updated_at=100 WHERE user_id=1")
    await store._conn.execute("UPDATE users SET updated_at=200 WHERE user_id=2")
    await store._conn.commit()

    users = await store.list_users()
    assert [u["user_id"] for u in users] == [2, 1]  # most recently active first
    assert users[0]["username"] == "b"
    assert "posts" in users[0] and "channels" in users[0]
    assert users[0]["last_active"] == 200


@pytest.mark.asyncio
async def test_list_users_limit_offset(store: StateStore) -> None:
    for uid in (1, 2, 3):
        await store.ensure_user(uid)
    page = await store.list_users(limit=2, offset=0)
    assert len(page) == 2


@pytest.mark.asyncio
async def test_get_user_profile_status_breakdown(store: StateStore) -> None:
    await store.ensure_user(1, username="a", first_name="A")
    prof = await store.get_user_profile(1)
    # All five statuses present and summing to total posts.
    assert set(prof["posts_by_status"]) == {
        "pending", "sending", "sent", "failed", "cancelled",
    }
    assert sum(prof["posts_by_status"].values()) == prof["posts"]


@pytest.mark.asyncio
async def test_all_user_ids_returns_every_id(store: StateStore) -> None:
    await store.ensure_user(1)
    await store.ensure_user(2)
    await store.ensure_user(3)
    ids = await store.all_user_ids()
    assert sorted(ids) == [1, 2, 3]
    assert all(isinstance(i, int) for i in ids)


@pytest.mark.asyncio
async def test_all_user_ids_empty(store: StateStore) -> None:
    assert await store.all_user_ids() == []
