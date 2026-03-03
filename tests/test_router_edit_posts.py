from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from telegram.router import EditStates, build_router

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
class EditFlowHarness:
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
            payload["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
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

    async def feed_photo(
        self,
        file_id: str,
        *,
        update_id: int,
        message_id: int,
        caption: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1_700_000_000,
                "chat": {"id": PRIVATE_CHAT_ID, "type": "private"},
                "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
                "photo": [
                    {
                        "file_id": file_id,
                        "file_unique_id": f"{file_id}_unique",
                        "width": 100,
                        "height": 100,
                    }
                ],
            },
        }
        if caption is not None:
            payload["message"]["caption"] = caption
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload))

    async def get_state(self) -> str | None:
        return await self.dispatcher.storage.get_state(self.storage_key)

    def last_call(self) -> Any:
        return self.bot.calls[-1]


def _short_id(value: str) -> str:
    return value[:8]


@pytest_asyncio.fixture
async def edit_flow() -> EditFlowHarness:
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

    yield EditFlowHarness(
        bot=bot,
        dispatcher=dispatcher,
        store=store,
        storage_key=StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
        conn=conn,
    )

    await conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_edit_command_lists_only_editable_pending_posts(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    editable_post_id = await edit_flow.store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Editable post",
        entities_json=None,
    )
    _, recurring_post_id = await edit_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc + 3600,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    await edit_flow.feed_message("/edit", update_id=1, message_id=10)

    call = edit_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr(
        "ru",
        "edit_list_header",
        lines=tr(
            "ru",
            "edit_list_item",
            post_id=_short_id(editable_post_id),
            where="Test channel",
            local_time="31.12.2099 09:30",
            kind=tr("ru", "kind_text"),
            preview="Editable post",
        ),
    )
    callbacks = [button.callback_data for row in call.reply_markup.inline_keyboard for button in row]
    assert f"qedit:{editable_post_id}" in callbacks
    assert recurring_post_id not in call.text


@pytest.mark.asyncio
async def test_edit_text_flow_updates_pending_text_post(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await edit_flow.store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Original text",
        entities_json=None,
    )

    await edit_flow.feed_message(f"/edit {_short_id(post_id)}", update_id=1, message_id=10)
    assert await edit_flow.get_state() == EditStates.choosing_field.state

    await edit_flow.feed_callback(f"eact:text:{post_id}", update_id=2, message_id=50)
    assert await edit_flow.get_state() == EditStates.entering_text.state

    await edit_flow.feed_message("Обновлённый текст", update_id=3, message_id=11)

    post = await edit_flow.store.get_scheduled_post(post_id)
    assert await edit_flow.get_state() is None
    assert post is not None
    assert post.text == "Обновлённый текст"
    assert edit_flow.last_call().text == tr("ru", "edit_text_updated_ok", post_id=_short_id(post_id))


@pytest.mark.asyncio
async def test_edit_time_manual_flow_updates_pending_post(edit_flow: EditFlowHarness) -> None:
    original_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    updated_at_utc = int(datetime(2099, 12, 31, 7, 0, tzinfo=timezone.utc).timestamp())
    post_id = await edit_flow.store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=original_at_utc,
        text="Original text",
        entities_json=None,
    )

    await edit_flow.feed_message(f"/edit {_short_id(post_id)}", update_id=1, message_id=10)
    await edit_flow.feed_callback(f"eact:time:{post_id}", update_id=2, message_id=50)

    assert await edit_flow.get_state() == EditStates.entering_datetime.state

    await edit_flow.feed_message("31.12.2099 10:00", update_id=3, message_id=11)

    post = await edit_flow.store.get_scheduled_post(post_id)
    assert await edit_flow.get_state() is None
    assert post is not None
    assert post.scheduled_at_utc == updated_at_utc
    assert edit_flow.last_call().text == tr(
        "ru",
        "edit_time_updated_ok",
        post_id=_short_id(post_id),
        local_time="31.12.2099 10:00",
        tz_name="Europe/Moscow",
    )


@pytest.mark.asyncio
async def test_edit_media_flow_replaces_media_and_keeps_caption(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await edit_flow.store.create_scheduled_media_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        caption="Старая подпись",
        caption_entities_json=None,
        caption_above=True,
        media_items=[{"type": "photo", "file_id": "old-photo"}],
    )

    await edit_flow.feed_message(f"/edit {_short_id(post_id)}", update_id=1, message_id=10)
    await edit_flow.feed_callback(f"eact:media:{post_id}", update_id=2, message_id=50)

    assert await edit_flow.get_state() == EditStates.collecting_media.state

    await edit_flow.feed_photo("new-photo", update_id=3, message_id=11)
    await edit_flow.feed_callback("smedia:done", update_id=4, message_id=51)

    post = await edit_flow.store.get_scheduled_post(post_id)
    assert await edit_flow.get_state() is None
    assert post is not None
    assert post.caption == "Старая подпись"
    assert post.caption_above == 1
    assert await edit_flow.store.get_post_media(post_id) == [{"type": "photo", "file_id": "new-photo"}]


@pytest.mark.asyncio
async def test_edit_command_rejects_ambiguous_short_id(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    shared_prefix = "abcdef12"
    await edit_flow.store._insert_scheduled_text_post(
        post_id=f"{shared_prefix}000000000000000000000001",
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="First post",
        entities_json=None,
        created_at=scheduled_at_utc - 100,
    )
    await edit_flow.store._insert_scheduled_text_post(
        post_id=f"{shared_prefix}000000000000000000000002",
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc + 60,
        text="Second post",
        entities_json=None,
        created_at=scheduled_at_utc - 50,
    )
    await edit_flow.store._conn.commit()

    await edit_flow.feed_message(f"/edit {shared_prefix}", update_id=1, message_id=10)

    assert edit_flow.last_call().text == tr("ru", "edit_post_ambiguous")


@pytest.mark.asyncio
async def test_edit_command_rejects_recurring_post(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    _, post_id = await edit_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    await edit_flow.feed_message(f"/edit {_short_id(post_id)}", update_id=1, message_id=10)

    assert edit_flow.last_call().text == tr("ru", "edit_post_recurring_blocked")


@pytest.mark.asyncio
async def test_delete_command_lists_only_deletable_pending_posts(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    deletable_post_id = await edit_flow.store.create_scheduled_text_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="Delete me",
        entities_json=None,
    )
    _, recurring_post_id = await edit_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc + 3600,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    await edit_flow.feed_message("/delete", update_id=1, message_id=10)

    call = edit_flow.last_call()
    assert isinstance(call, SendMessage)
    assert call.text == tr(
        "ru",
        "delete_list_header",
        lines=tr(
            "ru",
            "delete_list_item",
            post_id=_short_id(deletable_post_id),
            where="Test channel",
            local_time="31.12.2099 09:30",
            kind=tr("ru", "kind_text"),
            preview="Delete me",
        ),
    )
    callbacks = [button.callback_data for row in call.reply_markup.inline_keyboard for button in row]
    assert f"qdelask:{deletable_post_id}" in callbacks
    assert recurring_post_id not in call.text


@pytest.mark.asyncio
async def test_delete_confirm_flow_removes_pending_post_and_media(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post_id = await edit_flow.store.create_scheduled_media_post(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        caption="Caption",
        caption_entities_json=None,
        caption_above=False,
        media_items=[{"type": "photo", "file_id": "photo-1"}],
    )

    await edit_flow.feed_message(f"/delete {_short_id(post_id)}", update_id=1, message_id=10)
    assert edit_flow.last_call().text == tr(
        "ru",
        "delete_confirm",
        post_id=_short_id(post_id),
        where="Test channel",
        local_time="31.12.2099 09:30",
        tz_name="Europe/Moscow",
        kind=tr("ru", "kind_media", count=1),
        preview="Caption",
    )

    await edit_flow.feed_callback(f"qdelyes:{post_id}", update_id=2, message_id=50)

    assert await edit_flow.store.get_scheduled_post(post_id) is None
    assert await edit_flow.store.get_post_media(post_id) == []
    assert edit_flow.last_call().text == tr("ru", "delete_post_ok", post_id=_short_id(post_id))


@pytest.mark.asyncio
async def test_delete_command_rejects_ambiguous_short_id(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    shared_prefix = "abcdef12"
    await edit_flow.store._insert_scheduled_text_post(
        post_id=f"{shared_prefix}000000000000000000000001",
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc,
        text="First post",
        entities_json=None,
        created_at=scheduled_at_utc - 100,
    )
    await edit_flow.store._insert_scheduled_text_post(
        post_id=f"{shared_prefix}000000000000000000000002",
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        scheduled_at_utc=scheduled_at_utc + 60,
        text="Second post",
        entities_json=None,
        created_at=scheduled_at_utc - 50,
    )
    await edit_flow.store._conn.commit()

    await edit_flow.feed_message(f"/delete {shared_prefix}", update_id=1, message_id=10)

    assert edit_flow.last_call().text == tr("ru", "delete_post_ambiguous")


@pytest.mark.asyncio
async def test_delete_command_rejects_recurring_post(edit_flow: EditFlowHarness) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    _, post_id = await edit_flow.store.create_recurring_series(
        user_id=USER_ID,
        chat_id=DESTINATION_CHAT_ID,
        interval_type="daily",
        time_of_day_minutes=9 * 60,
        timezone="Europe/Moscow",
        start_at_utc=scheduled_at_utc,
        kind="text",
        text="Recurring post",
        entities_json=None,
    )

    await edit_flow.feed_message(f"/delete {_short_id(post_id)}", update_id=1, message_id=10)

    assert edit_flow.last_call().text == tr("ru", "delete_post_recurring_blocked")
