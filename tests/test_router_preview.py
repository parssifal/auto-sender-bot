from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import DeleteMessage, SendMediaGroup, SendMessage, SendPhoto, SendVideo
from aiogram.types import Update

from core.db import open_db
from core.state import StateStore
from core.time_picker import TimePicker
from telegram.router import build_router

USER_ID = 1001
PRIVATE_CHAT_ID = USER_ID
DESTINATION_CHAT_ID = -2001
BOT_ID = 42


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []
        self.deleted: list[tuple[int, int]] = []
        self.sent_ids: list[int] = []
        self._next_id = 100

    def _new_message(self) -> _FakeMessage:
        self._next_id += 1
        self.sent_ids.append(self._next_id)
        return _FakeMessage(self._next_id)

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, DeleteMessage):
            self.deleted.append((method.chat_id, method.message_id))
            return True
        if isinstance(method, SendMediaGroup):
            return [self._new_message() for _ in method.media]
        if isinstance(method, (SendMessage, SendPhoto, SendVideo)):
            return self._new_message()
        return True


@pytest_asyncio.fixture
async def preview_flow():
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    await store.ensure_user(USER_ID)
    await store.set_user_language(USER_ID, "ru")
    await store.set_user_timezone(USER_ID, "Europe/Moscow")
    await store.upsert_destination(
        DESTINATION_CHAT_ID, "channel", "Test channel", "test_channel", "administrator", True
    )
    await store.link_user_destination(USER_ID, DESTINATION_CHAT_ID, "link")

    bot = FakeBot()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(store))

    yield {
        "bot": bot,
        "dispatcher": dispatcher,
        "store": store,
        "storage_key": StorageKey(bot_id=BOT_ID, chat_id=PRIVATE_CHAT_ID, user_id=USER_ID),
    }

    await conn.close()
    await bot.session.close()


async def _feed_qview(flow, post_id: str, *, update_id: int) -> None:
    payload = {
        "update_id": update_id,
        "callback_query": {
            "id": f"q{update_id}",
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Test"},
            "chat_instance": "ci",
            "data": f"qview:{post_id}",
            "message": {
                "message_id": 5,
                "date": 1_700_000_000,
                "chat": {"id": PRIVATE_CHAT_ID, "type": "private"},
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
                "text": "stub",
            },
        },
    }
    await flow["dispatcher"].feed_update(flow["bot"], Update.model_validate(payload))


@pytest.mark.asyncio
async def test_second_preview_deletes_first_preview_messages(preview_flow) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post1 = await preview_flow["store"].create_scheduled_text_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc,
        text="First post", entities_json=None,
    )
    post2 = await preview_flow["store"].create_scheduled_text_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc + 60,
        text="Second post", entities_json=None,
    )

    await _feed_qview(preview_flow, post1, update_id=1)
    first_preview_ids = list(preview_flow["bot"].sent_ids)
    assert first_preview_ids, "first preview should have sent messages"
    assert preview_flow["bot"].deleted == []  # nothing to delete on the first preview

    await _feed_qview(preview_flow, post2, update_id=2)

    deleted_ids = [mid for (_chat, mid) in preview_flow["bot"].deleted]
    for mid in first_preview_ids:
        assert mid in deleted_ids, f"message {mid} from first preview should be deleted"


@pytest.mark.asyncio
async def test_media_preview_replaced_by_next_preview(preview_flow) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    media_post = await preview_flow["store"].create_scheduled_media_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc,
        caption="Album", caption_entities_json=None, caption_above=False,
        media_items=[{"type": "photo", "file_id": "p1"}, {"type": "photo", "file_id": "p2"}],
    )
    text_post = await preview_flow["store"].create_scheduled_text_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc + 60,
        text="Next", entities_json=None,
    )

    await _feed_qview(preview_flow, media_post, update_id=1)
    media_preview_ids = list(preview_flow["bot"].sent_ids)  # info msg + album messages
    assert len(media_preview_ids) >= 2

    await _feed_qview(preview_flow, text_post, update_id=2)

    deleted_ids = [mid for (_chat, mid) in preview_flow["bot"].deleted]
    for mid in media_preview_ids:
        assert mid in deleted_ids


@pytest.mark.asyncio
async def test_first_preview_does_not_delete_anything(preview_flow) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    post1 = await preview_flow["store"].create_scheduled_text_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc,
        text="Only post", entities_json=None,
    )
    await _feed_qview(preview_flow, post1, update_id=1)
    assert preview_flow["bot"].deleted == []


@pytest.mark.asyncio
async def test_preview_has_queue_navigation_buttons(preview_flow) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    ids = [
        await preview_flow["store"].create_scheduled_text_post(
            user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc + 60 * i,
            text=f"Post {i}", entities_json=None,
        )
        for i in range(3)
    ]

    def nav_row(flow) -> list[tuple[str, str]]:
        info = next(m for m in flow["bot"].calls if isinstance(m, SendMessage) and m.reply_markup is not None)
        (row,) = info.reply_markup.inline_keyboard
        return [(b.text, b.callback_data) for b in row]

    noop = TimePicker.NOOP_CALLBACK

    await _feed_qview(preview_flow, ids[0], update_id=1)
    assert nav_row(preview_flow) == [("·", noop), ("1 / 3", noop), ("➡️", f"qview:{ids[1]}")]

    preview_flow["bot"].calls.clear()
    await _feed_qview(preview_flow, ids[1], update_id=2)
    assert nav_row(preview_flow) == [
        ("⬅️", f"qview:{ids[0]}"),
        ("2 / 3", noop),
        ("➡️", f"qview:{ids[2]}"),
    ]

    preview_flow["bot"].calls.clear()
    await _feed_qview(preview_flow, ids[2], update_id=3)
    assert nav_row(preview_flow) == [("⬅️", f"qview:{ids[1]}"), ("3 / 3", noop), ("·", noop)]


@pytest.mark.asyncio
async def test_single_post_preview_has_no_nav_row(preview_flow) -> None:
    scheduled_at_utc = int(datetime(2099, 12, 31, 6, 30, tzinfo=timezone.utc).timestamp())
    only_post = await preview_flow["store"].create_scheduled_text_post(
        user_id=USER_ID, chat_id=DESTINATION_CHAT_ID, scheduled_at_utc=scheduled_at_utc,
        text="Only post", entities_json=None,
    )
    await _feed_qview(preview_flow, only_post, update_id=1)
    assert all(m.reply_markup is None for m in preview_flow["bot"].calls if isinstance(m, SendMessage))
