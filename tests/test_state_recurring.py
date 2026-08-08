import sqlite3

import pytest

from core.db import open_db
from core.state import StateStore


EXPECTED_PATTERN_COLUMNS = {
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

EXPECTED_INSTANCE_COLUMNS = {
    "pattern_id",
    "post_id",
    "ordinal",
    "scheduled_for_utc",
    "created_at",
}


async def _seed_recurring_pattern(
    store: StateStore,
    *,
    current_count: int = 1,
    is_active: bool = True,
) -> str:
    await store.ensure_user(123)
    await store.upsert_destination(
        -1001,
        "channel",
        "Recurring destination",
        "recurring_destination",
        "administrator",
        True,
    )
    return await store.create_recurring_pattern(
        user_id=123,
        chat_id=-1001,
        interval_type="weekdays",
        weekdays_mask=31,
        time_of_day_minutes=9 * 60 + 30,
        timezone="Europe/Moscow",
        start_at_utc=1_700_000_000,
        max_occurrences=10,
        current_count=current_count,
        is_active=is_active,
    )


@pytest.mark.asyncio
async def test_weekdays_mask_rejected_for_non_weekdays_intervals() -> None:
    # weekdays_mask is only honoured by interval_type='weekdays' (scheduler.py:84).
    # A mask on daily/weekly is stored but silently ignored, so reject it at the
    # boundary instead of leaving a dead contract.
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        await store.upsert_destination(
            -1001, "channel", "Dest", "dest", "administrator", True,
        )
        for interval in ("daily", "weekly"):
            with pytest.raises(ValueError, match="weekdays_mask"):
                await store.create_recurring_pattern(
                    user_id=123, chat_id=-1001, interval_type=interval,
                    weekdays_mask=31, time_of_day_minutes=600,
                    timezone="Europe/Moscow", start_at_utc=1_700_000_000,
                )
            with pytest.raises(ValueError, match="weekdays_mask"):
                await store.create_recurring_series(
                    user_id=123, chat_id=-1001, interval_type=interval,
                    weekdays_mask=31, time_of_day_minutes=600,
                    timezone="Europe/Moscow", start_at_utc=1_700_000_000,
                    kind="text", text="x", entities_json=None,
                )
        # Legitimate uses still work: weekdays+mask, and weekly with no mask.
        wk = await store.create_recurring_pattern(
            user_id=123, chat_id=-1001, interval_type="weekdays",
            weekdays_mask=31, time_of_day_minutes=600,
            timezone="Europe/Moscow", start_at_utc=1_700_000_000,
        )
        assert wk
        weekly = await store.create_recurring_pattern(
            user_id=123, chat_id=-1001, interval_type="weekly",
            time_of_day_minutes=600, timezone="Europe/Moscow",
            start_at_utc=1_700_000_000,
        )
        assert weekly
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_creates_recurring_patterns_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(recurring_patterns)")
        assert {str(column["name"]) for column in columns} == EXPECTED_PATTERN_COLUMNS

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
async def test_state_store_migrate_creates_recurring_instances_schema() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()

        columns = await conn.execute_fetchall("PRAGMA table_info(recurring_instances)")
        assert {str(column["name"]) for column in columns} == EXPECTED_INSTANCE_COLUMNS

        foreign_keys = await conn.execute_fetchall("PRAGMA foreign_key_list(recurring_instances)")
        assert {str(foreign_key["table"]) for foreign_key in foreign_keys} == {"recurring_patterns", "scheduled_posts"}

        indexes = await conn.execute_fetchall("PRAGMA index_list(recurring_instances)")
        assert {str(index["name"]) for index in indexes} >= {"idx_recurring_instances_pattern_scheduled"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_migrate_is_idempotent_for_recurring_patterns_and_instances() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        pattern_id = await _seed_recurring_pattern(store)
        post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_000,
            text="Recurring payload",
            entities_json=None,
        )
        await store.create_recurring_instance(pattern_id, post_id, 1, 1_700_000_000)

        await store.migrate()

        pattern_cur = await conn.execute(
            """
            SELECT interval_type, weekdays_mask, time_of_day_minutes, timezone, max_occurrences, current_count, is_active
            FROM recurring_patterns
            WHERE id=?
            """,
            (pattern_id,),
        )
        try:
            pattern_row = await pattern_cur.fetchone()
        finally:
            await pattern_cur.close()
        assert pattern_row is not None
        assert pattern_row["interval_type"] == "weekdays"
        assert pattern_row["weekdays_mask"] == 31
        assert pattern_row["time_of_day_minutes"] == 9 * 60 + 30
        assert pattern_row["timezone"] == "Europe/Moscow"
        assert pattern_row["max_occurrences"] == 10
        assert pattern_row["current_count"] == 1
        assert pattern_row["is_active"] == 1

        instance_cur = await conn.execute(
            """
            SELECT pattern_id, post_id, ordinal, scheduled_for_utc
            FROM recurring_instances
            WHERE post_id=?
            """,
            (post_id,),
        )
        try:
            instance_row = await instance_cur.fetchone()
        finally:
            await instance_cur.close()
        assert instance_row is not None
        assert instance_row["pattern_id"] == pattern_id
        assert instance_row["post_id"] == post_id
        assert instance_row["ordinal"] == 1
        assert instance_row["scheduled_for_utc"] == 1_700_000_000
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_recurring_instances_enforce_unique_post_mapping() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        pattern_id = await _seed_recurring_pattern(store)
        post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_000,
            text="Recurring payload",
            entities_json=None,
        )
        await store.create_recurring_instance(pattern_id, post_id, 1, 1_700_000_000)

        with pytest.raises(sqlite3.IntegrityError):
            await store.create_recurring_instance(pattern_id, post_id, 2, 1_700_000_000)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_recurring_pattern_crud_uses_public_methods() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        pattern_id = await _seed_recurring_pattern(store)

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.id == pattern_id
        assert pattern.user_id == 123
        assert pattern.chat_id == -1001
        assert pattern.interval_type == "weekdays"
        assert pattern.weekdays_mask == 31
        assert pattern.time_of_day_minutes == 9 * 60 + 30
        assert pattern.timezone == "Europe/Moscow"
        assert pattern.max_occurrences == 10
        assert pattern.current_count == 1
        assert pattern.is_active is True

        active_patterns = await store.list_user_recurring(123)
        assert [item.id for item in active_patterns] == [pattern_id]

        assert await store.update_recurring_count(pattern_id, 3) is True
        assert await store.update_recurring_count("missing-pattern", 1) is False
        updated_pattern = await store.get_recurring_pattern(pattern_id)
        assert updated_pattern is not None
        assert updated_pattern.current_count == 3

        assert await store.delete_recurring_pattern(pattern_id) is True
        assert await store.delete_recurring_pattern(pattern_id) is False

        deleted_pattern = await store.get_recurring_pattern(pattern_id)
        assert deleted_pattern is not None
        assert deleted_pattern.is_active is False

        assert await store.list_user_recurring(123) == []
        inactive_patterns = await store.list_user_recurring(123, include_inactive=True)
        assert [item.id for item in inactive_patterns] == [pattern_id]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_list_user_recurring_summaries_returns_next_pending_post() -> None:
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
        pattern_id, post_id = await store.create_recurring_series(
            user_id=123,
            chat_id=-1001,
            interval_type="daily",
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_700_000_000,
            kind="text",
            text="Recurring payload",
            entities_json=None,
        )

        summaries = await store.list_user_recurring_summaries(123, offset=0, limit=10)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.pattern.id == pattern_id
        assert summary.destination_title == "Recurring destination"
        assert summary.destination_username == "recurring_destination"
        assert summary.next_post_id == post_id
        assert summary.next_scheduled_at_utc == 1_700_000_000
        assert summary.next_post_status == "pending"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_list_user_recurring_summaries_filters_by_user_and_active_state() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        active_pattern_id = await _seed_recurring_pattern(store)
        active_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_000,
            text="Recurring payload",
            entities_json=None,
        )
        await store.create_recurring_instance(active_pattern_id, active_post_id, 1, 1_700_000_000)

        await store.ensure_user(999)
        other_pattern_id = await store.create_recurring_pattern(
            user_id=999,
            chat_id=-1001,
            interval_type="weekly",
            weekdays_mask=None,
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_700_000_500,
        )
        other_post_id = await store.create_scheduled_text_post(
            user_id=999,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_500,
            text="Other payload",
            entities_json=None,
        )
        await store.create_recurring_instance(other_pattern_id, other_post_id, 1, 1_700_000_500)

        inactive_pattern_id = await _seed_recurring_pattern(store)
        inactive_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_900,
            text="Inactive payload",
            entities_json=None,
        )
        await store.create_recurring_instance(inactive_pattern_id, inactive_post_id, 1, 1_700_000_900)
        assert await store.cancel_recurring_pattern(user_id=123, pattern_id=inactive_pattern_id) is True

        active_summaries = await store.list_user_recurring_summaries(123, offset=0, limit=10)
        assert [item.pattern.id for item in active_summaries] == [active_pattern_id]

        all_summaries = await store.list_user_recurring_summaries(123, offset=0, limit=10, include_inactive=True)
        assert {item.pattern.id for item in all_summaries} == {active_pattern_id, inactive_pattern_id}
        assert {item.pattern.user_id for item in all_summaries} == {123}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_recurring_instance_lookup_and_due_query() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        active_pattern_id = await _seed_recurring_pattern(store)

        due_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_100,
            text="due payload",
            entities_json=None,
        )
        due_instance = await store.create_recurring_instance(active_pattern_id, due_post_id, 1, 1_700_000_100)

        future_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_300,
            text="future payload",
            entities_json=None,
        )
        await store.create_recurring_instance(active_pattern_id, future_post_id, 2, 1_700_000_300)

        cancelled_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_050,
            text="cancelled payload",
            entities_json=None,
        )
        await store.create_recurring_instance(active_pattern_id, cancelled_post_id, 3, 1_700_000_050)
        assert await store.cancel_post(123, cancelled_post_id) is True

        inactive_pattern_id = await _seed_recurring_pattern(store)
        inactive_post_id = await store.create_scheduled_text_post(
            user_id=123,
            chat_id=-1001,
            scheduled_at_utc=1_700_000_090,
            text="inactive payload",
            entities_json=None,
        )
        await store.create_recurring_instance(inactive_pattern_id, inactive_post_id, 1, 1_700_000_090)
        assert await store.delete_recurring_pattern(inactive_pattern_id) is True

        lookup = await store.get_recurring_instance_by_post_id(due_post_id)
        assert lookup == due_instance
        assert await store.get_recurring_instance_by_post_id("missing-post") is None

        due_instances = await store.get_due_recurring_instances(1_700_000_200, limit=10)
        assert [item.post_id for item in due_instances] == [due_post_id]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_create_recurring_series_persists_media_pattern_post_and_instance() -> None:
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

        pattern_id, post_id = await store.create_recurring_series(
            user_id=123,
            chat_id=-1001,
            interval_type="weekly",
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_700_000_000,
            kind="media",
            caption="Recurring media",
            caption_entities_json=None,
            caption_above=True,
            media_items=[{"type": "photo", "file_id": "photo-1"}, {"type": "video", "file_id": "video-2"}],
        )

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.interval_type == "weekly"
        assert pattern.current_count == 1
        assert pattern.is_active is True

        post = await store.get_scheduled_post(post_id)
        assert post is not None
        assert post.kind == "media"
        assert post.caption == "Recurring media"
        assert post.caption_above == 1
        assert post.scheduled_at_utc == 1_700_000_000
        assert await store.get_post_media(post_id) == [
            {"type": "photo", "file_id": "photo-1"},
            {"type": "video", "file_id": "video-2"},
        ]

        instance = await store.get_recurring_instance_by_post_id(post_id)
        assert instance is not None
        assert instance.pattern_id == pattern_id
        assert instance.ordinal == 1
        assert instance.scheduled_for_utc == 1_700_000_000
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_cancel_recurring_pattern_deactivates_and_cancels_pending_post() -> None:
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

        pattern_id, post_id = await store.create_recurring_series(
            user_id=123,
            chat_id=-1001,
            interval_type="daily",
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_700_000_000,
            kind="text",
            text="Recurring payload",
            entities_json=None,
        )

        assert await store.cancel_recurring_pattern(user_id=123, pattern_id=pattern_id) is True

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False

        post = await store.get_scheduled_post(post_id)
        assert post is not None
        assert post.status == "cancelled"

        assert await store.list_pending_posts(123, limit=10) == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_state_store_cancel_recurring_pattern_is_idempotent_and_owner_scoped() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await store.ensure_user(123)
        await store.ensure_user(999)
        await store.upsert_destination(
            -1001,
            "channel",
            "Recurring destination",
            "recurring_destination",
            "administrator",
            True,
        )

        pattern_id, _ = await store.create_recurring_series(
            user_id=123,
            chat_id=-1001,
            interval_type="weekly",
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_700_000_000,
            kind="text",
            text="Recurring payload",
            entities_json=None,
        )

        assert await store.cancel_recurring_pattern(user_id=999, pattern_id=pattern_id) is False
        foreign_pattern = await store.get_recurring_pattern(pattern_id)
        assert foreign_pattern is not None
        assert foreign_pattern.is_active is True

        assert await store.cancel_recurring_pattern(user_id=123, pattern_id=pattern_id) is True
        assert await store.cancel_recurring_pattern(user_id=123, pattern_id=pattern_id) is True

        stopped_pattern = await store.get_recurring_pattern(pattern_id)
        assert stopped_pattern is not None
        assert stopped_pattern.is_active is False
    finally:
        await conn.close()
