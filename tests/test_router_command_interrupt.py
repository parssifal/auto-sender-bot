from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Update

from core.db import open_db
from core.state import StateStore
from telegram.i18n import tr
from telegram.handlers.states import (
    DestinationsStates,
    DraftStates,
    EditStates,
    LanguageStates,
    ScheduleStates,
    TimezoneStates,
)
from telegram.router import build_router

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
class InterruptHarness:
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

    async def feed_photo(self, *, update_id: int, message_id: int) -> None:
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": PRIVATE_CHAT_ID, "type": "private"},
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
                "photo": [
                    {"file_id": "photo-1", "file_unique_id": "u1", "width": 100, "height": 100},
                ],
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

    async def get_state(self) -> str | None:
        return await self.dispatcher.storage.get_state(self.storage_key)

    async def get_data(self) -> dict[str, Any]:
        return await self.dispatcher.storage.get_data(self.storage_key)

    def last_call(self) -> Any:
        return self.bot.calls[-1]

    async def reach_collecting_post(self) -> None:
        await self.feed_message("/schedule", update_id=1, message_id=10)
        await self.feed_callback(f"sdsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)
        await self.feed_message("31.12.2099 09:30", update_id=3, message_id=11)
        assert await self.get_state() == ScheduleStates.collecting_post.state

    async def reach_entering_datetime(self) -> None:
        await self.feed_message("/schedule", update_id=1, message_id=10)
        await self.feed_callback(f"sdsel:{DESTINATION_CHAT_ID}", update_id=2, message_id=50)
        assert await self.get_state() == ScheduleStates.entering_datetime.state


@pytest_asyncio.fixture
async def interrupt_flow() -> InterruptHarness:
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

    yield InterruptHarness(
        bot=bot,
        dispatcher=dispatcher,
        store=store,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
    )

    await conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_slash_schedule_interrupts_collecting_post(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_message("/schedule", update_id=10, message_id=20)

    # /schedule must interrupt the compose flow and restart destination selection.
    assert await interrupt_flow.get_state() == ScheduleStates.choosing_destination.state
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "choose_destination")


@pytest.mark.asyncio
async def test_slash_queue_interrupts_collecting_post(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_message("/queue", update_id=10, message_id=20)

    # /queue must reach the queue command handler, not be swallowed as post text.
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "queue_empty")
    assert call.text != tr("ru", "text_saved")


@pytest.mark.asyncio
async def test_menu_button_interrupts_collecting_post(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_message(tr("ru", "menu_schedule"), update_id=10, message_id=20)

    # A reply-keyboard menu button must interrupt the compose flow.
    assert await interrupt_flow.get_state() == ScheduleStates.choosing_destination.state
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "choose_destination")


@pytest.mark.asyncio
async def test_plain_text_still_collected(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_message("hello", update_id=10, message_id=20)

    # Plain text is still collected as the post body.
    assert await interrupt_flow.get_state() == ScheduleStates.collecting_post.state
    data = await interrupt_flow.get_data()
    assert data.get("draft_text") == "hello"
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "text_saved")


@pytest.mark.asyncio
async def test_media_still_collected(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_photo(update_id=10, message_id=20)

    # Media (text=None) must still be collected.
    assert await interrupt_flow.get_state() == ScheduleStates.collecting_post.state
    data = await interrupt_flow.get_data()
    assert len(data.get("media_items", [])) == 1
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "media_added", count=1)


@pytest.mark.asyncio
async def test_command_interrupts_entering_datetime(interrupt_flow: InterruptHarness) -> None:
    await interrupt_flow.reach_entering_datetime()

    await interrupt_flow.feed_message("/queue", update_id=10, message_id=20)

    # /queue while entering a datetime must not be parsed as a datetime.
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "queue_empty")
    assert call.text != tr("ru", "invalid_datetime_format")


# T-16: a stale sdsel: destination button must be ignored outside
# ScheduleStates.choosing_destination, not hijack the current flow.
@pytest.mark.asyncio
async def test_stale_dest_select_ignored_off_choosing_destination(
    interrupt_flow: InterruptHarness,
) -> None:
    await interrupt_flow.reach_collecting_post()

    await interrupt_flow.feed_callback(
        f"sdsel:{DESTINATION_CHAT_ID}", update_id=10, message_id=20
    )

    # The stale button must not jump the compose flow into datetime entry.
    assert await interrupt_flow.get_state() == ScheduleStates.collecting_post.state


# T-15: /timezone in a group chat must not leave TimezoneStates armed on the
# group's (chat, user) key, or every later group message gets timezone_invalid.
@pytest.mark.asyncio
async def test_timezone_command_leaves_no_state_in_group(interrupt_flow: InterruptHarness) -> None:
    group_chat_id = -5000
    group_key = StorageKey(bot_id=BOT_ID, chat_id=group_chat_id, user_id=USER_ID)
    payload = {
        "update_id": 30,
        "message": {
            "message_id": 40,
            "date": 1_700_000_000,
            "chat": {"id": group_chat_id, "type": "group", "title": "grp"},
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
            "text": "/timezone",
            "entities": [{"type": "bot_command", "offset": 0, "length": 9}],
        },
    }
    await interrupt_flow.dispatcher.feed_update(interrupt_flow.bot, Update.model_validate(payload))

    assert await interrupt_flow.dispatcher.storage.get_state(group_key) is None


# T-14: state-scoped message handlers outside shared.py that lacked the
# _not_command_or_menu filter and swallowed later-router commands as their input.
# /team_create lives in the last-registered router (teams), so it is swallowed by
# every one of these states before the fix. cmd_team_create clears state and
# replies team_create_usage, so a cleared state + that reply proves the interrupt.
@pytest.mark.parametrize(
    "state",
    [
        EditStates.entering_text,
        DraftStates.choosing_scope,
        TimezoneStates.waiting_tz,
        LanguageStates.waiting_lang,
        DestinationsStates.waiting_forward,
    ],
    ids=lambda s: s.state,
)
@pytest.mark.asyncio
async def test_command_interrupts_state_scoped_handler(
    interrupt_flow: InterruptHarness, state: Any
) -> None:
    await interrupt_flow.dispatcher.storage.set_state(interrupt_flow.storage_key, state)

    await interrupt_flow.feed_message("/team_create", update_id=10, message_id=20)

    # The command must fall through to its own handler, not be swallowed as input.
    assert await interrupt_flow.get_state() is None
    call = interrupt_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr("ru", "team_create_usage")
