from __future__ import annotations

import pytest
import pytest_asyncio

from core.db import open_db
from core.state import StateStore


@pytest_asyncio.fixture
async def store() -> StateStore:
    conn = await open_db(":memory:")
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


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
