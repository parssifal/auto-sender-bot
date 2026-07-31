import pytest
import pytest_asyncio

from core.db import open_db
from core.services import draft_svc
from core.state import StateStore


@pytest_asyncio.fixture
async def store():
    conn = await open_db(":memory:")            # sets row_factory=Row + foreign_keys ON
    state = StateStore(conn)
    await state.migrate()
    yield state
    await conn.close()


async def _seed_dest(store, chat_id, title, username):
    await store.upsert_destination(chat_id, "channel", title, username, "administrator", True)


@pytest.mark.asyncio
async def test_resolve_publishable_draft_returns_draft_for_author(store):
    author_id = 123
    await store.ensure_user(author_id)
    await _seed_dest(store, -1001, "Alpha", "alpha")
    draft_id = await store.create_draft(
        author_user_id=author_id,
        chat_id=-1001,
        kind="text",
        text="Hello world",
        entities_json=None,
    )

    draft = await draft_svc.resolve_publishable_draft(store, draft_id, author_id)

    assert draft is not None
    assert draft.id == draft_id
    assert draft.kind == "text"
    assert draft.text == "Hello world"


@pytest.mark.asyncio
async def test_resolve_publishable_draft_none_without_permission(store):
    owner_id = 123
    viewer_id = 456
    await store.ensure_user(owner_id)
    await store.ensure_user(viewer_id)
    await _seed_dest(store, -1001, "Alpha", "alpha")

    team_id = await store.create_team(owner_id, "Editorial")
    await store.upsert_team_member(team_id, viewer_id, "viewer")

    draft_id = await store.create_draft(
        team_id=team_id,
        author_user_id=owner_id,
        chat_id=-1001,
        kind="text",
        text="Team draft",
        entities_json=None,
    )

    # A viewer can see the draft but cannot publish it.
    draft = await draft_svc.resolve_publishable_draft(store, draft_id, viewer_id)
    assert draft is None

    # A total outsider (not a team member at all) also cannot publish it.
    outsider_id = 789
    await store.ensure_user(outsider_id)
    draft = await draft_svc.resolve_publishable_draft(store, draft_id, outsider_id)
    assert draft is None


@pytest.mark.asyncio
async def test_publish_draft_text_creates_scheduled_post(store):
    author_id = 123
    await store.ensure_user(author_id)
    await _seed_dest(store, -1001, "Alpha", "alpha")
    draft_id = await store.create_draft(
        author_user_id=author_id,
        chat_id=-1001,
        kind="text",
        text="Hello world",
        entities_json='[{"type":"bold"}]',
    )
    draft = await draft_svc.resolve_publishable_draft(store, draft_id, author_id)
    assert draft is not None

    post_id = await draft_svc.publish_draft(
        store, user_id=author_id, draft=draft, scheduled_at_utc=1_900_000_000
    )

    post = await store.get_scheduled_post(post_id)
    assert post is not None
    assert post.kind == "text"
    assert post.chat_id == -1001
    assert post.text == "Hello world"
    assert post.entities_json == '[{"type":"bold"}]'


@pytest.mark.asyncio
async def test_publish_draft_media_creates_scheduled_post(store):
    author_id = 123
    await store.ensure_user(author_id)
    await _seed_dest(store, -1001, "Alpha", "alpha")
    draft_id = await store.create_draft(
        author_user_id=author_id,
        chat_id=-1001,
        kind="media",
        caption="Look at this",
        caption_entities_json=None,
        caption_above=None,
        media_items=[{"type": "photo", "file_id": "photo-1"}],
    )
    draft = await draft_svc.resolve_publishable_draft(store, draft_id, author_id)
    assert draft is not None

    post_id = await draft_svc.publish_draft(
        store, user_id=author_id, draft=draft, scheduled_at_utc=1_900_000_000
    )

    post = await store.get_scheduled_post(post_id)
    assert post is not None
    assert post.kind == "media"
    assert post.chat_id == -1001
    assert post.caption == "Look at this"
