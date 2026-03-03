import pytest

from core.db import open_db
from core.state import StateStore


EXPECTED_COLUMNS = {
    "id",
    "user_id",
    "chat_id",
    "interval_type",
    "weekdays_mask",
    "time_of_day_minutes",
    "timezone",
    "start_at_utc",
    "end_at_utc",
    "max_occurrences",
    "current_count",
    "is_active",
    "created_at",
    "updated_at",
}


@pytest.mark.asyncio
async def test_state_store_migrate_creates_recurring_patterns_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(recurring_patterns)")
        assert {str(column["name"]) for column in columns} == EXPECTED_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(recurring_patterns)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"users", "destinations"}

        indexes = await conn.execute_fetchall("PRAGMA index_list(recurring_patterns)")
        assert {
            str(index["name"]) for index in indexes
        } >= {
            "sqlite_autoindex_recurring_patterns_1",
            "idx_recurring_patterns_user_active",
            "idx_recurring_patterns_chat_active",
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_is_idempotent_for_recurring_patterns() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        await store.upsert_destination(
            -1001,
            "channel",
            "Recurring destination",
            "recurring_destination",
            "administrator",
            True,
        )

        await conn.execute(
            """
            INSERT INTO recurring_patterns(
                id,
                user_id,
                chat_id,
                interval_type,
                weekdays_mask,
                time_of_day_minutes,
                timezone,
                start_at_utc,
                end_at_utc,
                max_occurrences,
                current_count,
                is_active,
                created_at,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pattern-1",
                123,
                -1001,
                "weekdays",
                31,
                9 * 60 + 30,
                "Europe/Moscow",
                1_700_000_000,
                None,
                10,
                1,
                1,
                1_700_000_000,
                1_700_000_000,
            ),
        )
        await conn.commit()

        await store.migrate()

        cur = await conn.execute(
            """
            SELECT interval_type, weekdays_mask, time_of_day_minutes, timezone, max_occurrences, current_count, is_active
            FROM recurring_patterns
            WHERE id=?
            """,
            ("pattern-1",),
        )
        try:
            row = await cur.fetchone()
        finally:
            await cur.close()
        assert row is not None
        assert row["interval_type"] == "weekdays"
        assert row["weekdays_mask"] == 31
        assert row["time_of_day_minutes"] == 9 * 60 + 30
        assert row["timezone"] == "Europe/Moscow"
        assert row["max_occurrences"] == 10
        assert row["current_count"] == 1
        assert row["is_active"] == 1
    finally:
        await conn.close()
