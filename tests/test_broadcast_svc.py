import pytest

from core.services import broadcast_svc


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


@pytest.mark.asyncio
async def test_create_broadcast_text_creates_one_post_per_chat(store):
    uid = 7
    await store.ensure_user(uid)
    await _seed_dest(store, uid, -100, "Alpha", "alpha")
    await _seed_dest(store, uid, -200, "Beta", None)
    content = broadcast_svc.PostContent(kind="text", text="hi", entities_json=None)
    post_ids = await broadcast_svc.create_broadcast(
        store, user_id=uid, chat_ids=[-100, -200], scheduled_at_utc=1_900_000_000, content=content
    )
    assert len(post_ids) == 2
    pending = await store.list_pending_posts(uid, limit=10)
    assert {p.chat_id for p in pending} == {-100, -200}
    assert {p.kind for p in pending} == {"text"}


@pytest.mark.asyncio
async def test_create_broadcast_media_passes_items_through(store):
    uid = 8
    await store.ensure_user(uid)
    await _seed_dest(store, uid, -100, "Alpha", "alpha")
    content = broadcast_svc.PostContent(
        kind="media", caption="c", caption_entities_json=None, caption_above=True,
        media_items=[{"type": "photo", "file_id": "F1"}],
    )
    post_ids = await broadcast_svc.create_broadcast(
        store, user_id=uid, chat_ids=[-100], scheduled_at_utc=1_900_000_000, content=content
    )
    assert len(post_ids) == 1
    # The media items must actually reach the created post, not just a post existing.
    assert await store.get_post_media(post_ids[0]) == [{"type": "photo", "file_id": "F1"}]
