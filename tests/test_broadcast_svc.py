import pytest
import pytest_asyncio

from core.db import open_db
from core.services import broadcast_svc
from core.state import StateStore


@pytest_asyncio.fixture
async def store():
    conn = await open_db(":memory:")            # sets row_factory=Row + foreign_keys ON
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


async def _seed_dest(store, user_id, chat_id, title, username):
    await store.upsert_destination(chat_id, "channel", title, username, "administrator", True)
    await store.link_user_destination(user_id, chat_id, "link")   # link table = what the DAL reads


@pytest.mark.asyncio
async def test_resolve_valid_destinations_filters_unknown_and_labels(store):
    uid = 42
    await store.ensure_user(uid)
    await _seed_dest(store, uid, -100, "Alpha", "alpha")
    await _seed_dest(store, uid, -200, "Beta", None)

    resolved = await broadcast_svc.resolve_valid_destinations(store, uid, [-100, -999, -200])

    # _normalize_selected_chat_ids dedupes/sorts ascending before filtering, so -999 (unknown)
    # is dropped and the known ids come back in ascending numeric order: -200 before -100.
    assert [chat_id for chat_id, _ in resolved] == [-200, -100]
    assert resolved[0][1]  # non-empty human label
