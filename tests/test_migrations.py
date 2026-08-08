import aiosqlite
import pytest

from core.migrate import MIGRATIONS_DIR, _split_statements, run_migrations
from core.state import StateStore

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


@pytest.mark.asyncio
async def test_rerun_is_noop_and_versions_stable():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await run_migrations(conn)
        first = await conn.execute_fetchall(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        await run_migrations(conn)  # second run
        second = await conn.execute_fetchall(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        assert [tuple(r) for r in first] == [tuple(r) for r in second]


@pytest.mark.asyncio
async def test_baseline_records_versions_without_dropping_data():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        # Pre-existing (pre-migration) schema + data. Only a *complete* set of
        # tables qualifies for baselining, so stub out every one of them.
        await conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, "
            "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        await conn.execute(
            "CREATE TABLE scheduled_posts (id TEXT PRIMARY KEY)"
        )
        for name in sorted(EXPECTED_TABLES - {"users", "scheduled_posts"}):
            await conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
        await conn.execute(
            "INSERT INTO users(user_id, created_at, updated_at) VALUES (42, 0, 0)"
        )
        await conn.commit()

        await run_migrations(conn)

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3, 4, 5}
        # Data intact, no DDL ran over the existing tables.
        row = await conn.execute_fetchall("SELECT user_id FROM users")
        assert [r[0] for r in row] == [42]


@pytest.mark.asyncio
async def test_partial_schema_is_completed_not_baselined():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        # Live DB that only ever received migrations 001 and 003:
        # scheduled_posts exists, teams/team_members do not.
        for name in ("001_users_destinations.sql", "003_posts.sql"):
            for stmt in _split_statements((MIGRATIONS_DIR / name).read_text()):
                await conn.execute(stmt)
        await conn.commit()

        # Must not stamp 1..5 over a half-built schema: the start path would
        # then fail on `no such table: team_members` with no recovery.
        await StateStore(conn).migrate()

        assert EXPECTED_TABLES <= await _tables(conn)


@pytest.mark.asyncio
async def test_failed_file_leaves_prior_versions_and_reruns_clean(tmp_path, monkeypatch):
    import core.migrate as migrate_mod

    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_a.sql").write_text("CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);")
    (mig / "002_b.sql").write_text("CREATE TABLE IF NOT EXISTS b (id INTEGER PRIMARY KEY);")
    # 003 is invalid on the first pass.
    bad = mig / "003_c.sql"
    bad.write_text("CREATE TABLE c (this is not valid sql;")

    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", mig)

    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        with pytest.raises(Exception):
            await migrate_mod.run_migrations(conn)

        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2}  # 3 not recorded

        # Fix file 003 and re-run; should complete and record version 3.
        bad.write_text("CREATE TABLE IF NOT EXISTS c (id INTEGER PRIMARY KEY);")
        await migrate_mod.run_migrations(conn)
        versions = {
            r[0]
            for r in await conn.execute_fetchall(
                "SELECT version FROM schema_migrations"
            )
        }
        assert versions == {1, 2, 3}
