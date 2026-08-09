import pytest

from core.services import draft_svc


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
@pytest.mark.parametrize(
    "caption_above, expected_caption_above",
    [(None, None), (True, 1), (False, 0)],
)
async def test_publish_draft_media_creates_scheduled_post(store, caption_above, expected_caption_above):
    author_id = 123
    await store.ensure_user(author_id)
    await _seed_dest(store, -1001, "Alpha", "alpha")
    media_items = [{"type": "photo", "file_id": "photo-1"}]
    draft_id = await store.create_draft(
        author_user_id=author_id,
        chat_id=-1001,
        kind="media",
        caption="Look at this",
        caption_entities_json=None,
        caption_above=caption_above,
        media_items=media_items,
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
    # caption_above branch in publish_draft must survive: None stays None, else bool->int.
    assert post.caption_above == expected_caption_above
    # The media items must carry through to the published post, not be silently lost.
    post_media = await store.get_post_media(post_id)
    assert post_media == await store.get_draft_media(draft.id)
    assert post_media == media_items


@pytest.mark.asyncio
@pytest.mark.parametrize("need", ["view", "edit", "delete", "publish"])
async def test_resolve_draft_by_ref_gates_on_permission(store, need):
    author_id = 111
    viewer_id = 222
    await store.ensure_user(author_id)
    await store.ensure_user(viewer_id)
    await _seed_dest(store, -2001, "Beta", "beta")

    team_id = await store.create_team(author_id, "Newsroom")
    await store.upsert_team_member(team_id, viewer_id, "viewer")

    draft_id = await store.create_draft(
        team_id=team_id,
        author_user_id=author_id,
        chat_id=-2001,
        kind="text",
        text="Team draft body",
        entities_json=None,
    )

    draft, perms = await draft_svc.resolve_draft_by_ref(store, viewer_id, draft_id, need=need)

    if need == "view":
        assert draft is not None
        assert draft.id == draft_id
        assert perms is not None
        assert perms.can_view is True
    else:
        assert draft is None
        assert perms is not None


@pytest.mark.asyncio
async def test_resolve_draft_by_ref_unknown_ref_returns_none_none(store):
    await store.ensure_user(1)
    draft, perms = await draft_svc.resolve_draft_by_ref(store, 1, "zzzz", need="view")
    assert draft is None and perms is None


@pytest.mark.asyncio
async def test_resolve_draft_by_id_gates(store):
    author_id = 333
    outsider_id = 444
    await store.ensure_user(author_id)
    await store.ensure_user(outsider_id)
    await _seed_dest(store, -3001, "Gamma", "gamma")

    draft_id = await store.create_draft(
        author_user_id=author_id,
        chat_id=-3001,
        kind="text",
        text="Personal draft",
        entities_json=None,
    )

    draft, perms = await draft_svc.resolve_draft_by_id(store, draft_id, author_id, need="publish")
    assert draft is not None
    assert draft.id == draft_id
    assert perms is not None
    assert perms.can_publish is True

    draft, perms = await draft_svc.resolve_draft_by_id(store, draft_id, outsider_id, need="publish")
    assert draft is None
    assert perms is not None
    assert perms.can_publish is False
