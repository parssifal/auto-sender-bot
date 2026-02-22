import pytest

from core.db import open_db
from core.state import StateStore


@pytest.mark.asyncio
async def test_state_store_persists_user_language() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        assert await store.get_user_language(123) is None

        await store.set_user_language(123, "ru")
        assert await store.get_user_language(123) == "ru"
    finally:
        await conn.close()
