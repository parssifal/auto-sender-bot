from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import Update

from core.db import open_db
from core.state import StateStore
from telegram.i18n import tr
from telegram.router import RepeatStates, build_router

USER_ID = 1001
PRIVATE_CHAT_ID = USER_ID
ALT_USER_ID = 2002
DESTINATION_CHAT_ID = -2001
BOT_ID = 42


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True

    async def me(self):
        return type("Me", (), {"id": BOT_ID})()

    async def get_chat_member(self, **kwargs):
        user_id = kwargs["user_id"]
        if user_id == BOT_ID:
            return type("Member", (), {"status": "administrator", "can_post_messages": True})()
        return type("Member", (), {"status": "administrator"})()


@dataclass
class RepeatFlowHarness:
    bot: FakeBot
    dispatcher: Dispatcher
    store: StateStore
    storage_key: StorageKey
    conn: Any

    async def feed_message(
        self,
        text: str,
        *,
        update_id: int,
        message_id: int,
        user_id: int = USER_ID,
        chat_id: int | None = None,
    ) -> None:
        effective_chat_id = user_id if chat_id is None else chat_id
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": effective_chat_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "text": text,
            },
        }
        if text.startswith("/"):
            payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def feed_callback(
        self,
        data: str,
        *,
        update_id: int,
        message_id: int,
        user_id: int = USER_ID,
        chat_id: int | None = None,
    ) -> None:
        effective_chat_id = user_id if chat_id is None else chat_id
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"q{update_id}",
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": 1_700_000_000,
                    "chat": {"id": effective_chat_id, "type": "private"},
                    "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
                    "text": "stub",
                },
            },
        }
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def get_state(self) -> str | None:
        return await self.dispatcher.storage.get_state(self.storage_key)

    async def get_data(self) -> dict[str, Any]:
        return await self.dispatcher.storage.get_data(self.storage_key)

    def last_call(self) -> Any:
        return self.bot.calls[-1]

    async def start_repeat(self) -> None:
        await self.feed_message("/repeat", update_id=1, message_id=10)


@pytest_asyncio.fixture
async def repeat_flow() -> RepeatFlowHarness:
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    await store.ensure_user(USER_ID)
    await store.set_user_language(USER_ID, "ru")
    await store.set_user_timezone(USER_ID, "Europe/Moscow")
    await store.ensure_user(ALT_USER_ID)
    await store.set_user_language(ALT_USER_ID, "ru")
    await store.set_user_timezone(ALT_USER_ID, "Europe/Moscow")
    await store.upsert_destination(
        DESTINATION_CHAT_ID,
        "channel",
        "Test channel",
        "test_channel",
        "administrator",
        True,
    )
    await store.link_user_destination(USER_ID, DESTINATION_CHAT_ID, "link")

    bot = FakeBot()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(store))

    yield RepeatFlowHarness(
        bot=bot,
        dispatcher=dispatcher,
        store=store,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
    )

    await conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_repeat_flow_starts_with_interval_selection(repeat_flow: RepeatFlowHarness) -> None:
    await repeat_flow.start_repeat()

    assert await repeat_flow.get_state() == RepeatStates.choosing_interval.state
    call = repeat_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "repeat_choose_interval")
    callbacks = [button.callback_data for row in call.reply_markup.inline_keyboard for button in row]
    assert "rint:daily" in callbacks
    assert "rint:weekly" in callbacks
    assert "rint:weekdays" in callbacks
    assert "rint:custom" in callbacks


@pytest.mark.asyncio
async def test_repeat_flow_quick_time_moves_to_destination_selection(repeat_flow: RepeatFlowHarness) -> None:
    await repeat_flow.start_repeat()
    await repeat_flow.feed_callback("rint:weekdays", update_id=2, message_id=50)

    assert await repeat_flow.get_state() == RepeatStates.entering_datetime.state
    interval_prompt = repeat_flow.last_call()
    assert isinstance(interval_prompt, SendMessage)
    assert interval_prompt.text == tr("ru", "repeat_enter_datetime")

    before_quick_calls = len(repeat_flow.bot.calls)
    await repeat_flow.feed_callback("tp:quick:next_monday", update_id=3, message_id=50)

    assert await repeat_flow.get_state() == RepeatStates.choosing_destination.state
    data = await repeat_flow.get_data()
    json.dumps(data)
    assert data["interval_type"] == "weekdays"
    assert int(data["scheduled_at_utc"]) > 0
    recent_calls = repeat_flow.bot.calls[before_quick_calls:]
    assert [type(item).__name__ for item in recent_calls] == [
        "AnswerCallbackQuery",
        "EditMessageReplyMarkup",
        "SendMessage",
    ]
    assert recent_calls[-1].text == tr("ru", "choose_destination")
    callbacks = [button.callback_data for row in recent_calls[-1].reply_markup.inline_keyboard for button in row]
    assert any(callback.startswith("rdsel:") for callback in callbacks)


@pytest.mark.asyncio
async def test_repeat_flow_creates_recurring_series_after_confirmation(repeat_flow: RepeatFlowHarness) -> None:
    await repeat_flow.start_repeat()
    await repeat_flow.feed_callback("rint:daily", update_id=2, message_id=50)
    await repeat_flow.feed_callback("tp:quick:next_monday", update_id=3, message_id=50)

    data_after_time = await repeat_flow.get_data()
    scheduled_at_utc = int(data_after_time["scheduled_at_utc"])

    await repeat_flow.feed_callback(f"rdsel:{DESTINATION_CHAT_ID}", update_id=4, message_id=51)

    assert await repeat_flow.get_state() == RepeatStates.collecting_post.state
    collect_prompt = repeat_flow.last_call()
    assert isinstance(collect_prompt, SendMessage)
    assert collect_prompt.text == tr("ru", "schedule_post_prompt")

    await repeat_flow.feed_message("Ежедневный пост", update_id=5, message_id=11)
    await repeat_flow.feed_callback("smedia:done", update_id=6, message_id=52)

    assert await repeat_flow.get_state() == RepeatStates.confirming.state
    confirm_call = repeat_flow.last_call()
    assert isinstance(confirm_call, SendMessage)
    assert tr("ru", "repeat_interval_daily") in confirm_call.text

    await repeat_flow.feed_callback("sconf:yes", update_id=7, message_id=53)

    assert await repeat_flow.get_state() is None
    final_call = repeat_flow.last_call()
    assert isinstance(final_call, SendMessage)
    assert "series=" in final_call.text

    patterns = await repeat_flow.store.list_user_recurring(USER_ID)
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.interval_type == "daily"
    assert pattern.chat_id == DESTINATION_CHAT_ID
    assert pattern.current_count == 1

    pending_posts = await repeat_flow.store.list_pending_posts(USER_ID, limit=10)
    assert len(pending_posts) == 1
    post = pending_posts[0]
    assert post.chat_id == DESTINATION_CHAT_ID
    assert post.kind == "text"
    assert post.text == "Ежедневный пост"
    assert post.scheduled_at_utc == scheduled_at_utc

    instance = await repeat_flow.store.get_recurring_instance_by_post_id(post.id)
    assert instance is not None
    assert instance.pattern_id == pattern.id
    assert instance.ordinal == 1


@pytest.mark.asyncio
async def test_repeat_flow_custom_interval_shows_alert_and_stays_in_interval_state(repeat_flow: RepeatFlowHarness) -> None:
    await repeat_flow.start_repeat()

    await repeat_flow.feed_callback("rint:custom", update_id=2, message_id=50)

    assert await repeat_flow.get_state() == RepeatStates.choosing_interval.state
    call = repeat_flow.last_call()
    assert isinstance(call, AnswerCallbackQuery)
    assert call.text == tr("ru", "repeat_custom_unavailable")
    assert call.show_alert is True


@pytest.mark.asyncio
async def test_repeat_cancel_command_stops_series_by_short_id(repeat_flow: RepeatFlowHarness) -> None:
    pattern_id, post_id = await repeat_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60 + 30,
        timezone="Europe/Moscow",
        start_at_utc=1_900_000_000,
        kind="text",
        text="Recurring text",
        entities_json=None,
    )

    await repeat_flow.feed_message(f"/repeat_cancel {pattern_id[:8]}", update_id=10, message_id=20)

    call = repeat_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "repeat_cancel_ok", pattern_id=pattern_id[:8])

    pattern = await repeat_flow.store.get_recurring_pattern(pattern_id)
    assert pattern is not None
    assert pattern.is_active is False

    post = await repeat_flow.store.get_scheduled_post(post_id)
    assert post is not None
    assert post.status == "cancelled"

    assert await repeat_flow.store.list_pending_posts(USER_ID, limit=10) == []


@pytest.mark.asyncio
async def test_repeats_command_lists_series_with_next_run_and_stop_button(repeat_flow: RepeatFlowHarness) -> None:
    start_at_utc = int(datetime(2026, 3, 10, 9, 30, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())
    pattern_id, _ = await repeat_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="weekly",
        time_of_day_minutes=9 * 60 + 30,
        timezone="Europe/Moscow",
        start_at_utc=start_at_utc,
        kind="text",
        text="Recurring text",
        entities_json=None,
    )

    await repeat_flow.feed_message("/repeats", update_id=20, message_id=30)

    call = repeat_flow.last_call()
    assert isinstance(call, SendMessage)
    assert tr("ru", "repeat_list_header", lines="X").split("\n", 1)[0] in call.text
    assert pattern_id[:8] in call.text
    assert "Test channel (@test_channel)" in call.text
    assert tr("ru", "repeat_interval_weekly") in call.text
    assert "10.03.2026 09:30 (Europe/Moscow)" in call.text
    callbacks = [button.callback_data for row in call.reply_markup.inline_keyboard for button in row]
    assert f"rstop:0:{pattern_id}" in callbacks


@pytest.mark.asyncio
async def test_repeats_command_supports_pagination_and_stop_callback(repeat_flow: RepeatFlowHarness) -> None:
    pattern_ids: list[str] = []
    for idx in range(6):
        pattern_id, _ = await repeat_flow.store.create_recurring_series(
            user_id=USER_ID,
            chat_id=DESTINATION_CHAT_ID,
            interval_type="daily" if idx % 2 == 0 else "weekdays",
            time_of_day_minutes=9 * 60 + 30,
            timezone="Europe/Moscow",
            start_at_utc=1_900_000_000 + idx * 3_600,
            kind="text",
            text=f"Recurring text {idx}",
            entities_json=None,
        )
        pattern_ids.append(pattern_id)
        await repeat_flow.conn.execute(
            "UPDATE recurring_patterns SET created_at=?, updated_at=? WHERE id=?",
            (100 + idx, 100 + idx, pattern_id),
        )
        await repeat_flow.conn.commit()

    await repeat_flow.feed_message("/repeats", update_id=21, message_id=31)
    first_page = repeat_flow.last_call()
    assert isinstance(first_page, SendMessage)
    first_callbacks = [button.callback_data for row in first_page.reply_markup.inline_keyboard for button in row]
    assert "rlpage:1" in first_callbacks
    assert pattern_ids[0][:8] not in first_page.text

    await repeat_flow.feed_callback("rlpage:1", update_id=22, message_id=31)

    second_page = repeat_flow.last_call()
    assert isinstance(second_page, EditMessageText)
    assert pattern_ids[0][:8] in second_page.text
    second_callbacks = [button.callback_data for row in second_page.reply_markup.inline_keyboard for button in row]
    assert f"rstop:1:{pattern_ids[0]}" in second_callbacks
    assert "rlpage:0" in second_callbacks

    await repeat_flow.feed_callback(f"rstop:1:{pattern_ids[0]}", update_id=23, message_id=31)

    stop_recent = repeat_flow.bot.calls[-2:]
    assert isinstance(stop_recent[0], AnswerCallbackQuery)
    assert isinstance(stop_recent[1], EditMessageText)
    assert pattern_ids[0][:8] not in stop_recent[1].text
    stopped_pattern = await repeat_flow.store.get_recurring_pattern(pattern_ids[0])
    assert stopped_pattern is not None
    assert stopped_pattern.is_active is False


@pytest.mark.asyncio
async def test_repeats_stop_callback_is_owner_scoped(repeat_flow: RepeatFlowHarness) -> None:
    pattern_id, _ = await repeat_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60 + 30,
        timezone="Europe/Moscow",
        start_at_utc=1_900_000_000,
        kind="text",
        text="Recurring text",
        entities_json=None,
    )

    await repeat_flow.feed_callback(
        f"rstop:0:{pattern_id}",
        update_id=24,
        message_id=40,
        user_id=ALT_USER_ID,
        chat_id=ALT_USER_ID,
    )

    recent = repeat_flow.bot.calls[-2:]
    assert isinstance(recent[0], AnswerCallbackQuery)
    assert recent[0].text == tr("ru", "repeat_cancel_missing")
    assert isinstance(recent[1], EditMessageText)
    assert recent[1].text == tr("ru", "repeat_list_empty")

    pattern = await repeat_flow.store.get_recurring_pattern(pattern_id)
    assert pattern is not None
    assert pattern.is_active is True
