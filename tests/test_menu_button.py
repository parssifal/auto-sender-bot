from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.types import MenuButtonDefault, MenuButtonWebApp

from telegram.menu_button import set_default_menu_button, set_user_menu_button


@pytest.mark.asyncio
async def test_default_menu_button_opens_app_when_url_set():
    bot = AsyncMock()
    await set_default_menu_button(bot, webapp_url="https://example.org/")

    bot.set_chat_menu_button.assert_awaited_once()
    kwargs = bot.set_chat_menu_button.await_args.kwargs
    button = kwargs["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    # Trailing slash is normalized; the app path is /app.
    assert button.web_app.url == "https://example.org/app"
    # No chat_id => this is the bot-wide default.
    assert "chat_id" not in kwargs


@pytest.mark.asyncio
async def test_default_menu_button_resets_when_url_unset():
    bot = AsyncMock()
    await set_default_menu_button(bot, webapp_url=None)

    button = bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert isinstance(button, MenuButtonDefault)


@pytest.mark.asyncio
async def test_user_menu_button_is_chat_scoped_and_localized():
    bot = AsyncMock()
    await set_user_menu_button(
        bot, chat_id=42, webapp_url="https://example.org", language="ru"
    )

    kwargs = bot.set_chat_menu_button.await_args.kwargs
    assert kwargs["chat_id"] == 42
    button = kwargs["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "Меню"
    assert button.web_app.url == "https://example.org/app"
