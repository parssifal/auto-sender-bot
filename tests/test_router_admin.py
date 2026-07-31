from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Update

from core.db import open_db
from core.state import StateStore
from telegram.admin import build_admin_router
from telegram.i18n import tr

ADMIN_ID = 42
OTHER_ID = 7
BOT_ID = 99
WEBAPP_URL = "https://example.com/admin"


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(f"{BOT_ID}:TEST")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        return True


async def _make_store() -> StateStore:
    conn = await open_db(":memory:")
    store = StateStore(conn)
    await store.migrate()
    for uid in (ADMIN_ID, OTHER_ID):
        await store.ensure_user(uid)
        await store.set_user_language(uid, "ru")
    return store


async def _feed_admin(dispatcher: Dispatcher, bot: FakeBot, user_id: int) -> None:
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "date": 1_700_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "text": "/admin",
            "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
        },
    }
    await dispatcher.feed_update(bot, Update.model_validate(payload))


@pytest.mark.asyncio
async def test_admin_opens_webapp_button() -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=WEBAPP_URL))

    await _feed_admin(dp, bot, ADMIN_ID)

    sends = [c for c in bot.calls if isinstance(c, SendMessage)]
    assert len(sends) == 1
    buttons = [b for row in sends[0].reply_markup.inline_keyboard for b in row]
    assert any(b.web_app and b.web_app.url == WEBAPP_URL for b in buttons)
    await store._conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_admin_ignored_for_non_admin() -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=WEBAPP_URL))

    await _feed_admin(dp, bot, OTHER_ID)

    assert [c for c in bot.calls if isinstance(c, SendMessage)] == []
    await store._conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_admin_reports_not_configured_without_url() -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=None))

    await _feed_admin(dp, bot, ADMIN_ID)

    sends = [c for c in bot.calls if isinstance(c, SendMessage)]
    assert len(sends) == 1
    assert sends[0].text == tr("ru", "admin_not_configured")
    assert sends[0].reply_markup is None
    await store._conn.close()
    await bot.session.close()


from aiogram.methods import EditMessageText  # noqa: E402
from telegram.handlers.states import AdminBroadcastStates  # noqa: E402
from core.services import admin_broadcast_svc  # noqa: E402


async def _feed_text(dispatcher: Dispatcher, bot: FakeBot, user_id: int, text: str, update_id: int) -> None:
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 20 + update_id,
            "date": 1_700_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    }
    await dispatcher.feed_update(bot, Update.model_validate(payload))


async def _feed_command(dispatcher: Dispatcher, bot: FakeBot, user_id: int, cmd: str, update_id: int) -> None:
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 30 + update_id,
            "date": 1_700_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "text": cmd,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(cmd)}],
        },
    }
    await dispatcher.feed_update(bot, Update.model_validate(payload))


async def _feed_callback(dispatcher: Dispatcher, bot: FakeBot, user_id: int, data: str, update_id: int) -> None:
    payload = {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "chat_instance": "ci",
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "data": data,
            "message": {
                "message_id": 40 + update_id,
                "date": 1_700_000_000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "Bot"},
                "text": "confirm?",
            },
        },
    }
    await dispatcher.feed_update(bot, Update.model_validate(payload))


@pytest.mark.asyncio
async def test_admin_broadcast_ignored_for_non_admin() -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=WEBAPP_URL))

    await _feed_command(dp, bot, OTHER_ID, "/admin_broadcast", 1)

    assert [c for c in bot.calls if isinstance(c, SendMessage)] == []
    await store._conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_admin_broadcast_full_flow(monkeypatch) -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=WEBAPP_URL))

    captured = {}

    async def fake_broadcast(store_arg, bot_arg, *, text, entities_json=None, **kw):
        captured["text"] = text
        return {"total": 2, "delivered": 2, "blocked": 0, "failed": 0}

    monkeypatch.setattr(admin_broadcast_svc, "broadcast_to_all", fake_broadcast)

    # 1) command -> prompt + state=collecting
    await _feed_command(dp, bot, ADMIN_ID, "/admin_broadcast", 1)
    ctx = dp.fsm.resolve_context(bot, ADMIN_ID, ADMIN_ID)
    assert await ctx.get_state() == AdminBroadcastStates.collecting.state

    # 2) text -> confirm message with inline buttons + state=confirming
    await _feed_text(dp, bot, ADMIN_ID, "Hello everyone", 2)
    assert await ctx.get_state() == AdminBroadcastStates.confirming.state
    sends = [c for c in bot.calls if isinstance(c, SendMessage)]
    confirm_send = sends[-1]
    datas = [b.callback_data for row in confirm_send.reply_markup.inline_keyboard for b in row]
    assert "abc:go" in datas and "abc:no" in datas

    # 3) confirm -> service called, report answered, state cleared
    await _feed_callback(dp, bot, ADMIN_ID, "abc:go", 3)
    assert captured["text"] == "Hello everyone"
    assert await ctx.get_state() is None
    report = [c for c in bot.calls if isinstance(c, SendMessage)][-1]
    assert "2" in report.text  # delivered count rendered

    await store._conn.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_admin_broadcast_cancel_button() -> None:
    store = await _make_store()
    bot = FakeBot()
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_admin_router(store=store, admin_ids=(ADMIN_ID,), webapp_url=WEBAPP_URL))

    await _feed_command(dp, bot, ADMIN_ID, "/admin_broadcast", 1)
    await _feed_text(dp, bot, ADMIN_ID, "Hi", 2)
    await _feed_callback(dp, bot, ADMIN_ID, "abc:no", 3)

    ctx = dp.fsm.resolve_context(bot, ADMIN_ID, ADMIN_ID)
    assert await ctx.get_state() is None
    edits = [c for c in bot.calls if isinstance(c, EditMessageText)]
    assert edits[-1].text == tr("ru", "admin_broadcast_cancelled")

    await store._conn.close()
    await bot.session.close()
