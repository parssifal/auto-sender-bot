from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from telegram.router import BroadcastStates, build_router

USER_ID = 1001
PRIVATE_CHAT_ID = USER_ID
BOT_ID = 42
DESTINATION_CHAT_IDS = [-2001, -2002]
ALL_DESTINATION_CHAT_IDS = [-2001, -2002, -2003, -2004, -2005, -2006]


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True


@dataclass
class BroadcastFlowHarness:
    bot: FakeBot
    dispatcher: Dispatcher
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

    async def set_state(self, state_name: str, *, data: dict[str, Any]) -> None:
        await self.dispatcher.storage.set_state(self.storage_key, state_name)
        await self.dispatcher.storage.set_data(self.storage_key, data)

    async def get_state(self) -> str | None:
        return await self.dispatcher.storage.get_state(self.storage_key)

    async def get_data(self) -> dict[str, Any]:
        return await self.dispatcher.storage.get_data(self.storage_key)

    def last_call(self) -> Any:
        return self.bot.calls[-1]


@pytest_asyncio.fixture
async def broadcast_flow() -> BroadcastFlowHarness:
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    await store.ensure_user(USER_ID)
    await store.set_user_language(USER_ID, "ru")
    await store.set_user_timezone(USER_ID, "Europe/Moscow")
    for index, chat_id in enumerate(ALL_DESTINATION_CHAT_IDS, start=1):
        await store.upsert_destination(
            chat_id,
            "channel",
            f"Channel {index}",
            f"channel_{index}",
            "administrator",
            True,
        )
        await store.link_user_destination(USER_ID, chat_id, "link")

    bot = FakeBot()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(store))

    yield BroadcastFlowHarness(
        bot=bot,
        dispatcher=dispatcher,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
    )

    await conn.close()
    await bot.session.close()


def _broadcast_seed_data(*, selected_chat_ids: list[int] | None = None, page: int = 0) -> dict[str, Any]:
    return {
        "selected_chat_ids": DESTINATION_CHAT_IDS.copy() if selected_chat_ids is None else selected_chat_ids,
        "dest_page": page,
        "selected_date": None,
        "calendar_year": None,
        "calendar_month": None,
    }


def _callback_data(call: Any) -> list[str]:
    return [
        button.callback_data
        for row in call.reply_markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None) is not None
    ]


def _destination_buttons(call: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in call.reply_markup.inline_keyboard:
        for button in row:
            callback_data = getattr(button, "callback_data", None)
            if isinstance(callback_data, str) and callback_data.startswith("bc:"):
                out.append((button.text, callback_data))
    return out


@pytest.mark.asyncio
async def test_broadcast_destination_picker_renders_checkboxes_and_pagination(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.choosing_destinations.state, data=_broadcast_seed_data(selected_chat_ids=[]))

    await broadcast_flow.feed_callback("bcpage:0", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.choosing_destinations.state
    call = broadcast_flow.last_call()
    assert isinstance(call, EditMessageText)
    assert call.text == tr("ru", "broadcast_choose_destinations", count=0)
    assert "bcpage:1" in _callback_data(call)
    assert "bcdone" in _callback_data(call)
    assert "scancel" in _callback_data(call)
    destination_buttons = _destination_buttons(call)
    assert len(destination_buttons) == 5
    assert all(text.startswith("⬜️ ") for text, _ in destination_buttons)


@pytest.mark.asyncio
async def test_broadcast_toggle_updates_selected_channels_and_count(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.choosing_destinations.state, data=_broadcast_seed_data(selected_chat_ids=[]))
    await broadcast_flow.feed_callback("bcpage:0", update_id=1, message_id=50)

    first_label, first_callback = _destination_buttons(broadcast_flow.last_call())[0]
    assert first_label.startswith("⬜️ ")
    selected_chat_id = int(first_callback.split(":")[1])

    await broadcast_flow.feed_callback(first_callback, update_id=2, message_id=50)

    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == [selected_chat_id]
    call = broadcast_flow.last_call()
    assert isinstance(call, EditMessageText)
    assert call.text == tr("ru", "broadcast_choose_destinations", count=1)
    assert f"bc:{selected_chat_id}:off" in _callback_data(call)


@pytest.mark.asyncio
async def test_broadcast_pagination_keeps_selected_channels(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.choosing_destinations.state, data=_broadcast_seed_data(selected_chat_ids=[]))
    await broadcast_flow.feed_callback("bcpage:0", update_id=1, message_id=50)

    selected_chat_id = int(_destination_buttons(broadcast_flow.last_call())[0][1].split(":")[1])
    await broadcast_flow.feed_callback(f"bc:{selected_chat_id}:on", update_id=2, message_id=50)
    await broadcast_flow.feed_callback("bcpage:1", update_id=3, message_id=50)

    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == [selected_chat_id]
    call = broadcast_flow.last_call()
    assert isinstance(call, EditMessageText)
    assert call.text == tr("ru", "broadcast_choose_destinations", count=1)
    assert "bcpage:0" in _callback_data(call)


@pytest.mark.asyncio
async def test_broadcast_done_requires_non_empty_selection(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.choosing_destinations.state, data=_broadcast_seed_data(selected_chat_ids=[]))

    await broadcast_flow.feed_callback("bcdone", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.choosing_destinations.state
    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == []
    alert_call = broadcast_flow.last_call()
    assert isinstance(alert_call, AnswerCallbackQuery)
    assert alert_call.text == tr("ru", "broadcast_choose_one")
    assert alert_call.show_alert is True


@pytest.mark.asyncio
async def test_broadcast_done_moves_to_datetime_prompt(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.choosing_destinations.state, data=_broadcast_seed_data())

    await broadcast_flow.feed_callback("bcdone", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.entering_datetime.state
    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == sorted(DESTINATION_CHAT_IDS)
    assert data["selected_date"] is None
    assert isinstance(broadcast_flow.bot.calls[-2], EditMessageReplyMarkup)
    prompt_call = broadcast_flow.last_call()
    assert isinstance(prompt_call, SendMessage)
    assert prompt_call.text == tr("ru", "enter_datetime")


@pytest.mark.asyncio
async def test_broadcast_calendar_callbacks_support_time_selection(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.entering_datetime.state, data=_broadcast_seed_data())

    await broadcast_flow.feed_callback("tp:date:20991231", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.selecting_time.state
    date_call = broadcast_flow.last_call()
    assert isinstance(date_call, EditMessageText)
    assert date_call.text == tr("ru", "schedule_time_prompt", date_label="31.12.2099")

    callbacks = [button.callback_data for row in date_call.reply_markup.inline_keyboard for button in row]
    assert "tp:time:0930" in callbacks
    assert "tp:back:calendar" in callbacks


@pytest.mark.asyncio
async def test_broadcast_back_from_time_returns_to_calendar(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.selecting_time.state, data={**_broadcast_seed_data(), "selected_date": "2099-12-31"})

    await broadcast_flow.feed_callback("tp:back:calendar", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.entering_datetime.state
    back_call = broadcast_flow.last_call()
    assert isinstance(back_call, EditMessageText)
    assert back_call.text == tr("ru", "enter_datetime")
    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == DESTINATION_CHAT_IDS


@pytest.mark.asyncio
async def test_broadcast_quick_datetime_moves_to_collecting_post(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.entering_datetime.state, data=_broadcast_seed_data())

    await broadcast_flow.feed_callback("tp:quick:next_monday", update_id=1, message_id=50)

    assert await broadcast_flow.get_state() == BroadcastStates.collecting_post.state
    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == DESTINATION_CHAT_IDS
    assert int(data["scheduled_at_utc"]) > int(datetime.now(timezone.utc).timestamp())
    call = broadcast_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "schedule_post_prompt")


@pytest.mark.asyncio
async def test_broadcast_manual_datetime_moves_to_collecting_post(broadcast_flow: BroadcastFlowHarness) -> None:
    await broadcast_flow.set_state(BroadcastStates.entering_datetime.state, data=_broadcast_seed_data())

    await broadcast_flow.feed_message("31.12.2099 09:30", update_id=1, message_id=10)

    assert await broadcast_flow.get_state() == BroadcastStates.collecting_post.state
    data = await broadcast_flow.get_data()
    assert data["selected_chat_ids"] == DESTINATION_CHAT_IDS
    assert data["scheduled_at_utc"] == int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    call = broadcast_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "schedule_post_prompt")
