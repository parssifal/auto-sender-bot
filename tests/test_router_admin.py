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
