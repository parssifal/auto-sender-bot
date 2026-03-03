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
        third_team_id = await store.create_team(456, "Gamma")

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
        assert [item.name for item in await store.list_writable_teams(123)] == ["Beta", "Alpha"]

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_035)
        gamma_editor_member = await store.upsert_team_member(third_team_id, 123, "editor")
        assert gamma_editor_member.role == "editor"
        assert gamma_editor_member.created_at == 1_700_000_035
        assert gamma_editor_member.updated_at == 1_700_000_035

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

        writable_teams = await store.list_writable_teams(123)
        assert [item.id for item in writable_teams] == [second_team_id, first_team_id, third_team_id]
        assert [item.name for item in writable_teams] == ["Beta", "Alpha", "Gamma"]

        assert await store.get_team("missing-team") is None
        assert await store.get_team_member_role(first_team_id, 999) is None
        assert [item.name for item in await store.list_owned_teams(456)] == ["Gamma"]
        assert [item.name for item in await store.list_writable_teams(456)] == ["Gamma"]
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
async def test_state_store_draft_crud_filters_accessible_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_000)
        for user_id in (123, 456, 789, 900):
            await store.ensure_user(user_id)
        await store.upsert_destination(
            -1001,
            "channel",
            "Draft destination",
            "draft_destination",
            "administrator",
            True,
        )

        team_alpha = await store.create_team(123, "Alpha")
        await store.upsert_team_member(team_alpha, 456, "editor")

        team_beta = await store.create_team(900, "Beta")
        await store.upsert_team_member(team_beta, 123, "viewer")

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_010)
        personal_draft_id = await store.create_draft(
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="My personal draft",
            entities_json=None,
        )
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_020)
        team_alpha_draft_id = await store.create_draft(
            team_id=team_alpha,
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="Alpha draft",
            entities_json=None,
        )
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_030)
        team_beta_draft_id = await store.create_draft(
            team_id=team_beta,
            author_user_id=900,
            chat_id=-1001,
            kind="text",
            text="Beta draft",
            entities_json=None,
        )
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_040)
        await store.create_draft(
            author_user_id=456,
            chat_id=-1001,
            kind="text",
            text="Other personal draft",
            entities_json=None,
        )

        all_drafts = await store.list_drafts(123, scope="all")
        assert [draft.id for draft in all_drafts] == [team_beta_draft_id, team_alpha_draft_id, personal_draft_id]

        own_drafts = await store.list_drafts(123, scope="mine")
        assert [draft.id for draft in own_drafts] == [team_alpha_draft_id, personal_draft_id]

        team_drafts = await store.list_drafts(123, scope="team")
        assert [draft.id for draft in team_drafts] == [team_beta_draft_id, team_alpha_draft_id]

        assert [draft.id for draft in await store.list_drafts(123, team_id=team_alpha)] == [team_alpha_draft_id]
        assert await store.list_drafts(456, team_id=team_beta) == []
        assert await store.list_drafts(123, team_id=team_beta, scope="mine") == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_get_draft_permissions_respects_personal_and_team_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_000)
        for user_id in (123, 456, 789):
            await store.ensure_user(user_id)
        await store.upsert_destination(
            -1001,
            "channel",
            "Draft destination",
            "draft_destination",
            "administrator",
            True,
        )

        team_id = await store.create_team(123, "Editorial")
        await store.upsert_team_member(team_id, 456, "editor")
        await store.upsert_team_member(team_id, 789, "viewer")

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_010)
        personal_draft_id = await store.create_draft(
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="Personal draft",
            entities_json=None,
        )
        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_020)
        team_draft_id = await store.create_draft(
            team_id=team_id,
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="Team draft",
            entities_json=None,
        )

        personal_permissions = await store.get_draft_permissions(personal_draft_id, 123)
        assert personal_permissions is not None
        assert personal_permissions.can_view is True
        assert personal_permissions.can_edit is True
        assert personal_permissions.can_delete is True
        assert personal_permissions.can_publish is True

        personal_outsider_permissions = await store.get_draft_permissions(personal_draft_id, 456)
        assert personal_outsider_permissions is not None
        assert personal_outsider_permissions.can_view is False
        assert personal_outsider_permissions.can_edit is False
        assert personal_outsider_permissions.can_delete is False
        assert personal_outsider_permissions.can_publish is False

        editor_permissions = await store.get_draft_permissions(team_draft_id, 456)
        assert editor_permissions is not None
        assert editor_permissions.can_view is True
        assert editor_permissions.can_edit is True
        assert editor_permissions.can_delete is True
        assert editor_permissions.can_publish is True

        viewer_permissions = await store.get_draft_permissions(team_draft_id, 789)
        assert viewer_permissions is not None
        assert viewer_permissions.can_view is True
        assert viewer_permissions.can_edit is False
        assert viewer_permissions.can_delete is False
        assert viewer_permissions.can_publish is False

        outsider_permissions = await store.get_draft_permissions(team_draft_id, 999)
        assert outsider_permissions is not None
        assert outsider_permissions.can_view is False
        assert outsider_permissions.can_edit is False
        assert outsider_permissions.can_delete is False
        assert outsider_permissions.can_publish is False

        assert await store.get_draft_permissions("missing-draft", 123) is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_create_update_and_delete_draft_enforce_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_000)
        for user_id in (123, 456, 789, 900):
            await store.ensure_user(user_id)
        await store.upsert_destination(
            -1001,
            "channel",
            "Draft destination",
            "draft_destination",
            "administrator",
            True,
        )
        await store.upsert_destination(
            -1002,
            "channel",
            "Updated destination",
            "updated_destination",
            "administrator",
            True,
        )

        team_id = await store.create_team(123, "Editorial")
        await store.upsert_team_member(team_id, 456, "editor")
        await store.upsert_team_member(team_id, 789, "viewer")

        with pytest.raises(ValueError, match="owner or editor"):
            await store.create_draft(
                team_id=team_id,
                author_user_id=789,
                chat_id=-1001,
                kind="text",
                text="Viewer should not create",
                entities_json=None,
            )

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_010)
        team_draft_id = await store.create_draft(
            team_id=team_id,
            author_user_id=123,
            chat_id=-1001,
            kind="media",
            caption="Initial media draft",
            caption_entities_json=None,
            caption_above=True,
            media_items=[
                {"type": "photo", "file_id": "photo-1"},
                {"type": "video", "file_id": "video-2"},
            ],
        )

        assert not await store.update_draft(
            team_draft_id,
            789,
            chat_id=-1002,
            kind="text",
            text="Viewer update",
            entities_json=None,
        )
        assert not await store.update_draft(
            team_draft_id,
            900,
            chat_id=-1002,
            kind="text",
            text="Outsider update",
            entities_json=None,
        )

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_020)
        assert await store.update_draft(
            team_draft_id,
            456,
            chat_id=-1002,
            kind="text",
            text="Editor converted to text",
            entities_json='[{"type":"italic"}]',
        )

        updated_to_text = await store.get_draft(team_draft_id)
        assert updated_to_text is not None
        assert updated_to_text.chat_id == -1002
        assert updated_to_text.kind == "text"
        assert updated_to_text.text == "Editor converted to text"
        assert updated_to_text.entities_json == '[{"type":"italic"}]'
        assert updated_to_text.caption is None
        assert updated_to_text.caption_above is None
        assert updated_to_text.updated_at == 1_700_000_020
        assert await store.get_draft_media(team_draft_id) == []

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_030)
        assert await store.update_draft(
            team_draft_id,
            123,
            chat_id=-1001,
            kind="media",
            caption="Owner converted back to media",
            caption_entities_json='[{"type":"bold"}]',
            caption_above=False,
            media_items=[{"type": "photo", "file_id": "photo-3"}],
        )

        updated_to_media = await store.get_draft(team_draft_id)
        assert updated_to_media is not None
        assert updated_to_media.chat_id == -1001
        assert updated_to_media.kind == "media"
        assert updated_to_media.caption == "Owner converted back to media"
        assert updated_to_media.caption_entities_json == '[{"type":"bold"}]'
        assert updated_to_media.caption_above == 0
        assert updated_to_media.text is None
        assert updated_to_media.updated_at == 1_700_000_030
        assert await store.get_draft_media(team_draft_id) == [{"type": "photo", "file_id": "photo-3"}]

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_040)
        personal_draft_id = await store.create_draft(
            author_user_id=123,
            chat_id=-1001,
            kind="text",
            text="Personal draft",
            entities_json=None,
        )

        monkeypatch.setattr("core.state.time.time", lambda: 1_700_000_050)
        assert await store.update_draft(
            personal_draft_id,
            123,
            chat_id=-1002,
            kind="text",
            text="Updated personal draft",
            entities_json='[{"type":"underline"}]',
        )
        updated_personal_draft = await store.get_draft(personal_draft_id)
        assert updated_personal_draft is not None
        assert updated_personal_draft.chat_id == -1002
        assert updated_personal_draft.text == "Updated personal draft"
        assert updated_personal_draft.entities_json == '[{"type":"underline"}]'
        assert updated_personal_draft.updated_at == 1_700_000_050

        assert not await store.delete_draft(team_draft_id, 789)
        assert await store.delete_draft(team_draft_id, 456)
        assert await store.get_draft(team_draft_id) is None
        assert await store.get_draft_media(team_draft_id) == []

        assert not await store.delete_draft(personal_draft_id, 456)
        assert await store.delete_draft(personal_draft_id, 123)
        assert await store.get_draft(personal_draft_id) is None
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
