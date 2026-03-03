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
async def test_state_store_migrate_is_idempotent_for_teams() -> None:
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

        owned_teams = await store.list_owned_teams(123)
        assert [item.id for item in owned_teams] == [second_team_id, first_team_id]
        assert [item.name for item in owned_teams] == ["Beta", "Alpha"]

        assert await store.get_team("missing-team") is None
        assert [item.name for item in await store.list_owned_teams(456)] == ["Gamma"]
    finally:
        await conn.close()
