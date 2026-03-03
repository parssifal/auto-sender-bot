from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

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
from telegram.router import ScheduleStates, build_router

USER_ID = 1001
PRIVATE_CHAT_ID = USER_ID
DESTINATION_CHAT_ID = -2001
BOT_ID = 42


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True


@dataclass
class ScheduleFlowHarness:
    bot: FakeBot
    dispatcher: Dispatcher
    store: StateStore
    storage_key: StorageKey
    conn: Any

    async def feed_message(self, text: str, *, update_id: int, message_id: int) -> None:
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": PRIVATE_CHAT_ID, "type": "private"},
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
                "text": text,
            },
        }
        if text.startswith("/"):
            payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def feed_callback(self, data: str, *, update_id: int, message_id: int) -> None:
        payload = {
            "update_id": update_id,
            "callback_query": {
                "id": f"q{update_id}",
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": 1_700_000_000,
                    "chat": {"id": PRIVATE_CHAT_ID, "type": "private"},
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

    async def start_schedule(self) -> None:
        await self.feed_message("/schedule", update_id=1, message_id=10)
        await self.feed_callback(f"sdsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)


@pytest_asyncio.fixture
async def schedule_flow() -> ScheduleFlowHarness:
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    await store.ensure_user(USER_ID)
    await store.set_user_language(USER_ID, "ru")
    await store.set_user_timezone(USER_ID, "Europe/Moscow")
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

    yield ScheduleFlowHarness(
        bot=bot,
        dispatcher=dispatcher,
        store=store,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
    )

    await conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_schedule_flow_enters_datetime_picker_after_destination_selection(schedule_flow: ScheduleFlowHarness) -> None:
    await schedule_flow.feed_message("/schedule", update_id=1, message_id=10)

    assert await schedule_flow.get_state() == ScheduleStates.choosing_destination.state
    choose_call = schedule_flow.last_call()
    assert isinstance(choose_call, SendMessage)
    assert choose_call.text == tr("ru", "choose_destination")
    assert choose_call.reply_markup.inline_keyboard[0][0].callback_data == f"sdsel:{DESTINATION_CHAT_ID}"

    await schedule_flow.feed_callback(f"sdsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)

    assert await schedule_flow.get_state() == ScheduleStates.entering_datetime.state
    data = await schedule_flow.get_data()
    json.dumps(data)
    assert data["chat_id"] == DESTINATION_CHAT_ID
    prompt_call = schedule_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert prompt_call.text == tr("ru", "enter_datetime")
    callbacks = [button.callback_data for row in prompt_call.reply_markup.inline_keyboard for button in row]
    assert any(callback.startswith("tp:nav:") for callback in callbacks)
    assert "tp:quick:next_monday" in callbacks


@pytest.mark.asyncio
async def test_schedule_flow_date_and_time_callbacks_move_to_collecting_post(schedule_flow: ScheduleFlowHarness) -> None:
    await schedule_flow.start_schedule()

    await schedule_flow.feed_callback("tp:date:20991231", update_id=3, message_id=51)

    assert await schedule_flow.get_state() == ScheduleStates.selecting_time.state
    date_call = schedule_flow.last_call()
    assert isinstance(date_call, EditMessageText)
    assert date_call.text == tr("ru", "schedule_time_prompt", date_label="31.12.2099")
    callbacks = [button.callback_data for row in date_call.reply_markup.inline_keyboard for button in row]
    assert "tp:time:0930" in callbacks
    assert "tp:back:calendar" in callbacks

    before_time_calls = len(schedule_flow.bot.calls)
    await schedule_flow.feed_callback("tp:time:0930", update_id=4, message_id=51)

    assert await schedule_flow.get_state() == ScheduleStates.collecting_post.state
    data = await schedule_flow.get_data()
    json.dumps(data)
    assert data["scheduled_at_utc"] == int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    recent_calls = schedule_flow.bot.calls[before_time_calls:]
    assert [type(item).__name__ for item in recent_calls] == [
        "AnswerCallbackQuery",
        "EditMessageReplyMarkup",
        "SendMessage",
    ]
    assert recent_calls[-1].text == tr("ru", "schedule_post_prompt")


@pytest.mark.asyncio
async def test_schedule_flow_back_from_time_returns_to_calendar(schedule_flow: ScheduleFlowHarness) -> None:
    await schedule_flow.start_schedule()
    await schedule_flow.feed_callback("tp:date:20991231", update_id=3, message_id=51)

    await schedule_flow.feed_callback("tp:back:calendar", update_id=4, message_id=51)

    assert await schedule_flow.get_state() == ScheduleStates.entering_datetime.state
    back_call = schedule_flow.last_call()
    assert isinstance(back_call, EditMessageText)
    assert back_call.text == tr("ru", "enter_datetime")
    callbacks = [button.callback_data for row in back_call.reply_markup.inline_keyboard for button in row]
    assert "tp:date:20991231" in callbacks
    assert "tp:quick:1h" in callbacks


@pytest.mark.asyncio
async def test_schedule_flow_manual_fallback_from_time_selection_works(schedule_flow: ScheduleFlowHarness) -> None:
    await schedule_flow.start_schedule()
    await schedule_flow.feed_callback("tp:date:20991231", update_id=3, message_id=51)

    await schedule_flow.feed_message("31.12.2099 09:30", update_id=4, message_id=11)

    assert await schedule_flow.get_state() == ScheduleStates.collecting_post.state
    data = await schedule_flow.get_data()
    assert data["scheduled_at_utc"] == int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    call = schedule_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "schedule_post_prompt")


@pytest.mark.asyncio
async def test_schedule_flow_quick_button_moves_to_collecting_post(schedule_flow: ScheduleFlowHarness) -> None:
    await schedule_flow.start_schedule()

    before_quick_calls = len(schedule_flow.bot.calls)
    await schedule_flow.feed_callback("tp:quick:next_monday", update_id=3, message_id=51)

    assert await schedule_flow.get_state() == ScheduleStates.collecting_post.state
    data = await schedule_flow.get_data()
    assert int(data["scheduled_at_utc"]) > int(datetime.now(timezone.utc).timestamp())
    recent_calls = schedule_flow.bot.calls[before_quick_calls:]
    assert [type(item).__name__ for item in recent_calls] == [
        "AnswerCallbackQuery",
        "EditMessageReplyMarkup",
        "SendMessage",
    ]
    assert recent_calls[-1].text == tr("ru", "schedule_post_prompt")
