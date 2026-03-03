import pytest

from core.db import open_db
from core.state import StateStore


EXPECTED_TEAM_COLUMNS = {
    "id",
    "owner_user_id",
    "name",
    "created_at",
    "updated_at",
}

EXPECTED_TEAM_MEMBER_COLUMNS = {
    "team_id",
    "user_id",
    "role",
    "created_at",
    "updated_at",
}

EXPECTED_DRAFT_COLUMNS = {
    "id",
    "team_id",
    "author_user_id",
    "chat_id",
    "kind",
    "text",
    "entities_json",
    "caption",
    "caption_entities_json",
    "caption_above",
    "created_at",
    "updated_at",
}

EXPECTED_DRAFT_MEDIA_COLUMNS = {
    "draft_id",
    "idx",
    "type",
    "file_id",
}


@pytest.mark.asyncio
async def test_state_store_migrate_creates_teams_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(teams)")
        assert {str(column["name"]) for column in columns} == EXPECTED_TEAM_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(teams)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"users"}

        indexes = await conn.execute_fetchall("PRAGMA index_list(teams)")
        assert {str(index["name"]) for index in indexes} >= {
            "sqlite_autoindex_teams_1",
            "idx_teams_owner_created",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_creates_team_members_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(team_members)")
        assert {str(column["name"]) for column in columns} == EXPECTED_TEAM_MEMBER_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(team_members)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"teams", "users"}

        indexes = await conn.execute_fetchall("PRAGMA index_list(team_members)")
        assert {str(index["name"]) for index in indexes} >= {
            "sqlite_autoindex_team_members_1",
            "idx_team_members_user_team",
            "idx_team_members_single_owner",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_creates_drafts_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(drafts)")
        assert {str(column["name"]) for column in columns} == EXPECTED_DRAFT_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(drafts)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"teams", "users", "destinations"}

        indexes = await conn.execute_fetchall("PRAGMA index_list(drafts)")
        assert {str(index["name"]) for index in indexes} >= {
            "sqlite_autoindex_drafts_1",
            "idx_drafts_author_created",
            "idx_drafts_team_created",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_creates_draft_media_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(draft_media)")
        assert {str(column["name"]) for column in columns} == EXPECTED_DRAFT_MEDIA_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(draft_media)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"drafts"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_is_idempotent_for_teams_and_backfills_owner_membership() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        team_id = await store.create_team(123, "Editorial")

        await store.migrate()

        team = await store.get_team(team_id)
        assert team is not None
        assert team.id == team_id
        assert team.owner_user_id == 123
        assert team.name == "Editorial"
        assert await store.get_team_member_role(team_id, 123) == "owner"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_is_idempotent_for_drafts() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        await store.upsert_destination(
            -1001,
            "channel",
            "Draft destination",
            "draft_destination",
            "administrator",
            True,
        )
        team_id = await store.create_team(123, "Editorial")
        draft_id = await store.create_draft(
            team_id=team_id,
            author_user_id=123,
            chat_id=-1001,
            kind="media",
            caption="Review this",
            caption_entities_json='[{"type":"bold"}]',
            caption_above=True,
            media_items=[{"type": "photo", "file_id": "photo-1"}],
        )

        await store.migrate()

        draft = await store.get_draft(draft_id)
        assert draft is not None
        assert draft.team_id == team_id
        assert draft.kind == "media"
        assert draft.caption == "Review this"
        assert draft.caption_entities_json == '[{"type":"bold"}]'
        assert draft.caption_above == 1
        assert await store.get_draft_media(draft_id) == [{"type": "photo", "file_id": "photo-1"}]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_team_crud_uses_public_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_000)
        await store.ensure_user(123)
        await store.ensure_user(456)

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_010)
        first_team_id = await store.create_team(123, "Alpha")
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_020)
        second_team_id = await store.create_team(123, "Beta")
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_030)
        await store.create_team(456, "Gamma")

        team = await store.get_team(first_team_id)
        assert team is not None
        assert team.id == first_team_id
        assert team.owner_user_id == 123
        assert team.name == "Alpha"
        assert team.created_at == 1_700_000_010
        assert team.updated_at == 1_700_000_010
        assert await store.get_team_member_role(first_team_id, 123) == "owner"

        owned_teams = await store.list_owned_teams(123)
        assert [item.id for item in owned_teams] == [second_team_id, first_team_id]
        assert [item.name for item in owned_teams] == ["Beta", "Alpha"]

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_040)
        editor_member = await store.upsert_team_member(first_team_id, 456, "editor")
        assert editor_member.role == "editor"
        assert editor_member.created_at == 1_700_000_040
        assert editor_member.updated_at == 1_700_000_040

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_050)
        viewer_member = await store.upsert_team_member(first_team_id, 456, "viewer")
        assert viewer_member.role == "viewer"
        assert viewer_member.created_at == 1_700_000_040
        assert viewer_member.updated_at == 1_700_000_050
        assert await store.get_team_member_role(first_team_id, 456) == "viewer"

        members = await store.list_team_members(first_team_id)
        assert [(member.user_id, member.role) for member in members] == [
            (123, "owner"),
            (456, "viewer"),
        ]

        assert await store.get_team("missing-team") is None
        assert await store.get_team_member_role(first_team_id, 999) is None
        assert [item.name for item in await store.list_owned_teams(456)] == ["Gamma"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_draft_storage_supports_text_and_media_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_000)
        await store.ensure_user(123)
        await store.upsert_destination(
            -1001,
            "channel",
            "Draft destination",
            "draft_destination",
            "administrator",
            True,
        )

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_010)
        text_draft_id = await store.create_draft(
            team_id=None,
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="Personal draft",
            entities_json='[{"type":"italic"}]',
        )
        team_id = await store.create_team(123, "Editorial")

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_020)
        media_draft_id = await store.create_draft(
            team_id=team_id,
            author_user_id=123,
            chat_id=-1001,
            kind="media",
            caption="Team draft",
            caption_entities_json='[{"type":"bold"}]',
            caption_above=True,
            media_items=[
                {"type": "photo", "file_id": "photo-1"},
                {"type": "video", "file_id": "video-2"},
            ],
        )

        text_draft = await store.get_draft(text_draft_id)
        assert text_draft is not None
        assert text_draft.team_id is None
        assert text_draft.author_user_id == 123
        assert text_draft.chat_id == -1001
        assert text_draft.kind == "text"
        assert text_draft.text == "Personal draft"
        assert text_draft.entities_json == '[{"type":"italic"}]'
        assert text_draft.caption is None
        assert text_draft.caption_above is None
        assert text_draft.created_at == 1_700_000_010
        assert text_draft.updated_at == 1_700_000_010
        assert await store.get_draft_media(text_draft_id) == []

        media_draft = await store.get_draft(media_draft_id)
        assert media_draft is not None
        assert media_draft.team_id == team_id
        assert media_draft.kind == "media"
        assert media_draft.caption == "Team draft"
        assert media_draft.caption_entities_json == '[{"type":"bold"}]'
        assert media_draft.caption_above == 1
        assert media_draft.text is None
        assert media_draft.created_at == 1_700_000_020
        assert media_draft.updated_at == 1_700_000_020
        assert await store.get_draft_media(media_draft_id) == [
            {"type": "photo", "file_id": "photo-1"},
            {"type": "video", "file_id": "video-2"},
        ]

        assert await store.get_draft("missing-draft") is None
        assert await store.get_draft_media("missing-draft") == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_backfills_owner_for_legacy_teams() -> None:
    conn = await open_db(":memory:")
    try:
        await conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT NULL,
                language TEXT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE teams (
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            """
        )
        await conn.execute(
            "INSERT INTO users(user_id, timezone, language, created_at, updated_at) VALUES(?, NULL, NULL, ?, ?)",
            (123, 1_700_000_000, 1_700_000_000),
        )
        await conn.execute(
            "INSERT INTO teams(id, owner_user_id, name, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            ("legacy-team", 123, "Legacy", 1_700_000_010, 1_700_000_010),
        )
        await conn.commit()

        store = StateStore(conn)
        await store.migrate()

        assert await store.get_team_member_role("legacy-team", 123) == "owner"
        members = await store.list_team_members("legacy-team")
        assert [(member.user_id, member.role) for member in members] == [(123, "owner")]

        await store.migrate()
        members_after_second_migrate = await store.list_team_members("legacy-team")
        assert [(member.user_id, member.role) for member in members_after_second_migrate] == [(123, "owner")]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_team_members_enforce_single_owner() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        await store.ensure_user(456)

        team_id = await store.create_team(123, "Editorial")

        with pytest.raises(ValueError, match="owner transfer"):
            await store.upsert_team_member(team_id, 456, "owner")

        with pytest.raises(ValueError, match="owner role cannot be changed"):
            await store.upsert_team_member(team_id, 123, "editor")
    finally:
        await conn.close()
