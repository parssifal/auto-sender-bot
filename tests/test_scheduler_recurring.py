from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.db import open_db
from core.scheduler import _process_due_post, calculate_next_occurrence
from core.state import RecurringPattern, StateStore

USER_ID = 123
CHAT_ID = -1001
BOT_ID = 777


class FakeBot:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._fail_send = fail_send

    async def me(self):
        return type("Me", (), {"id": BOT_ID})()

    async def get_chat_member(self, **kwargs):
        return type("Member", (), {"status": "administrator", "can_post_messages": True})()

    async def send_message(self, **kwargs):
        if self._fail_send:
            raise RuntimeError("send_message failed")
        self.calls.append(("send_message", kwargs))
        return True

    async def send_photo(self, **kwargs):
        if self._fail_send:
            raise RuntimeError("send_photo failed")
        self.calls.append(("send_photo", kwargs))
        return True

    async def send_video(self, **kwargs):
        if self._fail_send:
            raise RuntimeError("send_video failed")
        self.calls.append(("send_video", kwargs))
        return True

    async def send_media_group(self, **kwargs):
        if self._fail_send:
            raise RuntimeError("send_media_group failed")
        self.calls.append(("send_media_group", kwargs))
        return True


async def _seed_destination(store: StateStore) -> None:
    await store.ensure_user(USER_ID)
    await store.upsert_destination(
        CHAT_ID,
        "channel",
        "Recurring destination",
        "recurring_destination",
        "administrator",
        True,
    )


async def _seed_pattern(
    store: StateStore,
    *,
    interval_type: str,
    timezone_name: str,
    time_of_day_minutes: int,
    start_at_utc: int,
    weekdays_mask: int | None = None,
    max_occurrences: int | None = None,
    current_count: int = 1,
    end_at_utc: int | None = None,
) -> str:
    await _seed_destination(store)
    return await store.create_recurring_pattern(
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type=interval_type,
        weekdays_mask=weekdays_mask,
        time_of_day_minutes=time_of_day_minutes,
        timezone=timezone_name,
        start_at_utc=start_at_utc,
        end_at_utc=end_at_utc,
        max_occurrences=max_occurrences,
        current_count=current_count,
    )


@pytest.mark.asyncio
async def test_calculate_next_occurrence_skips_to_next_weekday() -> None:
    pattern = RecurringPattern(
        id="pattern",
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="weekdays",
        weekdays_mask=0b0011111,
        time_of_day_minutes=9 * 60 + 30,
        timezone="Europe/Moscow",
        start_at_utc=0,
        end_at_utc=None,
        max_occurrences=None,
        current_count=1,
        is_active=True,
        created_at=0,
        updated_at=0,
    )
    friday_local = datetime(2026, 3, 6, 9, 30, tzinfo=ZoneInfo("Europe/Moscow"))

    next_occurrence = calculate_next_occurrence(pattern, int(friday_local.timestamp()))
    next_local = datetime.fromtimestamp(next_occurrence, tz=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))

    assert next_local.year == 2026
    assert next_local.month == 3
    assert next_local.day == 9
    assert next_local.hour == 9
    assert next_local.minute == 30


@pytest.mark.asyncio
async def test_calculate_next_occurrence_preserves_local_time_across_dst_gap() -> None:
    pattern = RecurringPattern(
        id="pattern",
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="daily",
        weekdays_mask=None,
        time_of_day_minutes=2 * 60 + 30,
        timezone="Europe/Berlin",
        start_at_utc=0,
        end_at_utc=None,
        max_occurrences=None,
        current_count=1,
        is_active=True,
        created_at=0,
        updated_at=0,
    )
    before_dst_local = datetime(2026, 3, 28, 2, 30, tzinfo=ZoneInfo("Europe/Berlin"))

    next_occurrence = calculate_next_occurrence(pattern, int(before_dst_local.timestamp()))
    next_local = datetime.fromtimestamp(next_occurrence, tz=timezone.utc).astimezone(ZoneInfo("Europe/Berlin"))

    assert next_local.year == 2026
    assert next_local.month == 3
    assert next_local.day == 29
    assert next_local.hour == 3
    assert next_local.minute == 30


@pytest.mark.asyncio
async def test_calculate_next_occurrence_preserves_local_time_across_dst_fall_back() -> None:
    # T-47 (scheduler path, fall-back half): Europe/Berlin 2026-10-25, clocks go
    # 03:00 CEST -> 02:00 CET, so 02:30 exists twice. _build_local_datetime uses the
    # default fold=0, i.e. the FIRST 02:30 (CEST, +02:00). The daily series must keep
    # firing at 02:30 wall time, not silently move an hour. This is the intended,
    # non-rejecting behaviour on the internal recurrence path (parse_local_datetime
    # rejects only the spring-forward *gap*; ambiguous fall-back times are kept).
    pattern = RecurringPattern(
        id="pattern",
        user_id=USER_ID,
        chat_id=CHAT_ID,
        interval_type="daily",
        weekdays_mask=None,
        time_of_day_minutes=2 * 60 + 30,
        timezone="Europe/Berlin",
        start_at_utc=0,
        end_at_utc=None,
        max_occurrences=None,
        current_count=1,
        is_active=True,
        created_at=0,
        updated_at=0,
    )
    before_dst_local = datetime(2026, 10, 24, 2, 30, tzinfo=ZoneInfo("Europe/Berlin"))

    next_occurrence = calculate_next_occurrence(pattern, int(before_dst_local.timestamp()))
    next_local = datetime.fromtimestamp(next_occurrence, tz=timezone.utc).astimezone(ZoneInfo("Europe/Berlin"))

    assert next_local.year == 2026
    assert next_local.month == 10
    assert next_local.day == 25
    assert next_local.hour == 2
    assert next_local.minute == 30
    # fold=0 -> first occurrence, still CEST (+02:00); a fold=1 slip would read +01:00.
    assert next_local.utcoffset().total_seconds() == 2 * 3600


@pytest.mark.asyncio
async def test_process_due_post_ends_series_past_end_at_utc() -> None:
    # T-49: the end_at_utc branch (scheduler.py) was never executed by any test -
    # every helper call passed end_at_utc=None. When the next occurrence falls past
    # end_at_utc the series must stop instead of materializing beyond its window.
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        next_at = due_at + 86400  # Moscow has no DST: next daily occurrence is +1 day exactly.
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
            end_at_utc=next_at - 1,  # window closes one second before the next occurrence.
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Last one", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        post = await store.get_scheduled_post(post_id)
        assert post is not None

        await _process_due_post(bot=FakeBot(), store=store, post=post, now_utc=due_at)

        sent_post = await store.get_scheduled_post(post_id)
        assert sent_post is not None
        assert sent_post.status == "sent"
        assert await store.list_pending_posts(USER_ID, limit=10) == []
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_end_at_utc_boundary_is_inclusive() -> None:
    # T-49 boundary: the comparison is `next > end_at_utc`, so a next occurrence landing
    # exactly ON end_at_utc is still inside the window and must be materialized. A `>=`
    # slip would wrongly end the series one occurrence early.
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        next_at = due_at + 86400
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
            end_at_utc=next_at,  # end lands exactly on the next occurrence: inclusive.
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        post = await store.get_scheduled_post(post_id)
        assert post is not None

        await _process_due_post(bot=FakeBot(), store=store, post=post, now_utc=due_at)

        pending_posts = await store.list_pending_posts(USER_ID, limit=10)
        assert len(pending_posts) == 1
        assert pending_posts[0].scheduled_at_utc == next_at
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is True
        assert pattern.current_count == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_materializes_next_recurring_text_post() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)

        original_post = await store.get_scheduled_post(post_id)
        assert original_post is not None

        bot = FakeBot()
        await _process_due_post(bot=bot, store=store, post=original_post, now_utc=due_at)

        sent_post = await store.get_scheduled_post(post_id)
        assert sent_post is not None
        assert sent_post.status == "sent"
        assert bot.calls == [("send_message", {"chat_id": CHAT_ID, "text": "Recurring text"})]

        pending_posts = await store.list_pending_posts(USER_ID, limit=10)
        assert len(pending_posts) == 1
        next_post = pending_posts[0]
        assert next_post.id != post_id
        assert next_post.text == "Recurring text"
        assert next_post.scheduled_at_utc == int(
            datetime(2026, 3, 4, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
        )

        next_instance = await store.get_recurring_instance_by_post_id(next_post.id)
        assert next_instance is not None
        assert next_instance.pattern_id == pattern_id
        assert next_instance.ordinal == 2
        assert next_instance.scheduled_for_utc == next_post.scheduled_at_utc

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.current_count == 2
        assert pattern.is_active is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_materializes_next_recurring_media_post() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="weekly",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_media_post(
            USER_ID,
            CHAT_ID,
            due_at,
            "Recurring media",
            None,
            True,
            [{"type": "photo", "file_id": "photo-1"}, {"type": "video", "file_id": "video-2"}],
        )
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)

        post = await store.get_scheduled_post(post_id)
        assert post is not None

        bot = FakeBot()
        await _process_due_post(bot=bot, store=store, post=post, now_utc=due_at)

        pending_posts = await store.list_pending_posts(USER_ID, limit=10)
        assert len(pending_posts) == 1
        next_post = pending_posts[0]
        assert next_post.kind == "media"
        assert next_post.caption == "Recurring media"
        assert next_post.caption_above == 1
        assert next_post.scheduled_at_utc == int(
            datetime(2026, 3, 10, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp()
        )
        assert await store.get_post_media(next_post.id) == [
            {"type": "photo", "file_id": "photo-1"},
            {"type": "video", "file_id": "video-2"},
        ]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_stops_recurring_series_at_max_occurrences() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
            max_occurrences=1,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Final occurrence", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)

        post = await store.get_scheduled_post(post_id)
        assert post is not None

        await _process_due_post(bot=FakeBot(), store=store, post=post, now_utc=due_at)

        assert await store.list_pending_posts(USER_ID, limit=10) == []
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False
        assert pattern.current_count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_does_not_materialize_next_post_on_send_error() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Retry me", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)

        post = await store.get_scheduled_post(post_id)
        assert post is not None

        await _process_due_post(bot=FakeBot(fail_send=True), store=store, post=post, now_utc=due_at)

        pending_posts = await store.list_pending_posts(USER_ID, limit=10)
        assert len(pending_posts) == 1
        assert pending_posts[0].id == post_id
        assert pending_posts[0].next_retry_at_utc is not None

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.current_count == 1

        due_instances = await store.get_due_recurring_instances(now_utc=4_000_000_000, limit=10)
        assert [item.post_id for item in due_instances] == [post_id]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_skips_cancelled_recurring_post_loaded_before_stop() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Stop me", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)

        post = await store.get_scheduled_post(post_id)
        assert post is not None
        assert await store.cancel_recurring_pattern(user_id=USER_ID, pattern_id=pattern_id) is True

        bot = FakeBot()
        await _process_due_post(bot=bot, store=store, post=post, now_utc=due_at)

        cancelled_post = await store.get_scheduled_post(post_id)
        assert cancelled_post is not None
        assert cancelled_post.status == "cancelled"
        assert bot.calls == []
        assert await store.list_pending_posts(USER_ID, limit=10) == []

        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_skips_occurrences_missed_during_downtime() -> None:
    """Downtime must not turn a daily series into a burst: the next occurrence is
    rolled forward past `now`, and the skipped ones count against max_occurrences."""
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        tz = ZoneInfo("Europe/Moscow")
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=tz).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        original_post = await store.get_scheduled_post(post_id)
        assert original_post is not None

        # The bot was down for a month; the tick happens on 2026-04-02 12:00.
        now_utc = int(datetime(2026, 4, 2, 12, 0, tzinfo=tz).timestamp())
        await _process_due_post(bot=FakeBot(), store=store, post=original_post, now_utc=now_utc)

        pending_posts = await store.list_pending_posts(USER_ID, limit=50)
        assert len(pending_posts) == 1
        assert pending_posts[0].scheduled_at_utc == int(datetime(2026, 4, 3, 9, 30, tzinfo=tz).timestamp())

        next_instance = await store.get_recurring_instance_by_post_id(pending_posts[0].id)
        assert next_instance is not None
        assert next_instance.ordinal == 32
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_ends_series_when_downtime_consumes_max_occurrences() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        tz = ZoneInfo("Europe/Moscow")
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=tz).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
            max_occurrences=5,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        original_post = await store.get_scheduled_post(post_id)
        assert original_post is not None

        now_utc = int(datetime(2026, 4, 2, 12, 0, tzinfo=tz).timestamp())
        await _process_due_post(bot=FakeBot(), store=store, post=original_post, now_utc=now_utc)

        assert await store.list_pending_posts(USER_ID, limit=50) == []
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_never_marks_sent_without_next_occurrence() -> None:
    """'Post is sent' and 'the series continues' must commit together.

    Split across two transactions, a crash in between leaves a sent post, an
    active pattern and no successor — the series stops forever and nothing can
    find it again (get_due_recurring_instances only matches pending posts)."""
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        post = await store.get_scheduled_post(post_id)
        assert post is not None

        async def _boom(*args, **kwargs):
            raise RuntimeError("materialization write failed")

        # The store write that creates the next occurrence fails, whatever it is called.
        store.materialize_next_recurring_post = _boom
        store.mark_sent_and_materialize_next = _boom

        await _process_due_post(bot=FakeBot(), store=store, post=post, now_utc=due_at)

        sent_post = await store.get_scheduled_post(post_id)
        assert sent_post is not None
        successors = [row for row in await store.list_pending_posts(USER_ID, limit=10) if row.id != post_id]
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert (sent_post.status == "sent") == bool(successors), (
            f"orphaned series: status={sent_post.status}, "
            f"successors={len(successors)}, pattern_active={pattern.is_active}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mark_sent_and_materialize_next_rolls_back_as_one_transaction() -> None:
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        assert await store.claim_post_for_sending(post_id=post_id, now_utc=due_at) is True

        # Ordinal 2 is already taken, so the insert half of the transaction fails.
        blocker_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at + 86400, "Blocker", None)
        await store.create_recurring_instance(pattern_id, blocker_id, 2, due_at + 86400)

        with pytest.raises(sqlite3.IntegrityError):
            await store.mark_sent_and_materialize_next(
                post_id=post_id,
                sent_at_utc=due_at,
                pattern_id=pattern_id,
                next_ordinal=2,
                scheduled_for_utc=due_at + 86400,
            )

        claimed_post = await store.get_scheduled_post(post_id)
        assert claimed_post is not None
        assert claimed_post.status == "sending"
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.current_count == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_ends_series_when_active_posts_cap_is_reached() -> None:
    """Materialization is background work: there is nobody to hand a
    ResourceLimitError to, so the series ends visibly instead of stalling
    forever as an active pattern with no pending instance."""
    import core.limits as limits

    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        tz = ZoneInfo("Europe/Moscow")
        due_at = int(datetime(2026, 3, 3, 9, 30, tzinfo=tz).timestamp())
        pattern_id = await _seed_pattern(
            store,
            interval_type="daily",
            timezone_name="Europe/Moscow",
            time_of_day_minutes=9 * 60 + 30,
            start_at_utc=due_at,
        )
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Recurring text", None)
        await store.create_recurring_instance(pattern_id, post_id, 1, due_at)
        await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at + 86400, "Other pending", None)
        original_post = await store.get_scheduled_post(post_id)
        assert original_post is not None

        original_cap = limits.MAX_ACTIVE_POSTS_PER_USER
        limits.MAX_ACTIVE_POSTS_PER_USER = 1
        try:
            await _process_due_post(bot=FakeBot(), store=store, post=original_post, now_utc=due_at)
        finally:
            limits.MAX_ACTIVE_POSTS_PER_USER = original_cap

        sent_post = await store.get_scheduled_post(post_id)
        assert sent_post is not None
        assert sent_post.status == "sent"
        pattern = await store.get_recurring_pattern(pattern_id)
        assert pattern is not None
        assert pattern.is_active is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_marks_failed_after_max_attempts() -> None:
    # T-10: a post whose send keeps failing must stop retrying and free its quota slot,
    # not retry forever with a 3600s backoff cap while holding an active-posts slot.
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await _seed_destination(store)
        now0 = int(time.time())
        due_at = now0 - 10
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "Doomed", None)

        bot = FakeBot(fail_send=True)
        # next_retry_at is computed from real time.time(); step now past the max backoff
        # (3600s) each tick so every retry is re-claimable.
        now = now0
        statuses: list[str] = []
        for _ in range(6):
            post = await store.get_scheduled_post(post_id)
            assert post is not None
            await _process_due_post(bot=bot, store=store, post=post, now_utc=now)
            statuses.append((await store.get_scheduled_post(post_id)).status)
            now += 3700

        assert statuses == ["pending", "pending", "pending", "pending", "pending", "failed"]
        final = await store.get_scheduled_post(post_id)
        assert final.attempts == 6
        assert await store.count_active_posts(USER_ID) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_process_due_post_fails_broken_entities_without_retry() -> None:
    # T-11: a deterministic entities_json parse error can never be fixed by retrying, so
    # the post must go straight to 'failed' on the first attempt, not retry (up to 6x).
    conn = await open_db(":memory:")
    try:
        store = StateStore(conn)
        await store.migrate()
        await _seed_destination(store)
        due_at = int(time.time()) - 10
        post_id = await store.create_scheduled_text_post(USER_ID, CHAT_ID, due_at, "hi", "not json")

        post = await store.get_scheduled_post(post_id)
        assert post is not None
        await _process_due_post(bot=FakeBot(), store=store, post=post, now_utc=due_at)

        final = await store.get_scheduled_post(post_id)
        assert final.status == "failed"
        assert final.attempts == 1
    finally:
        await conn.close()
