import aiosqlite
import pytest

from core.migrate import run_migrations

EXPECTED_TABLES = {
    "users", "destinations", "user_destinations",
    "teams", "team_members", "team_invites",
    "scheduled_posts", "scheduled_post_media",
    "recurring_patterns", "recurring_instances",
    "drafts", "draft_media",
}


async def _tables(conn):
    rows = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_fresh_db_creates_all_tables_and_records_versions():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await run_migrations(conn)

        tables = await _tables(conn)
        assert EXPECTED_TABLES <= tables
        assert "schema_migrations" in tables

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3, 4, 5}
