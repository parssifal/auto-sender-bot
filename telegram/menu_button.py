from __future__ import annotations

from aiogram import Bot
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from telegram.i18n import tr


def _app_url(webapp_url: str) -> str:
    """URL the Mini App menu button opens (the per-user queue page)."""
    return f"{webapp_url.rstrip('/')}/app"


def _web_app_button(webapp_url: str, language: str | None) -> MenuButtonWebApp:
    return MenuButtonWebApp(
        text=tr(language, "menu_button_app"),
        web_app=WebAppInfo(url=_app_url(webapp_url)),
    )


async def set_default_menu_button(bot: Bot, *, webapp_url: str | None) -> None:
    """Set the bot-wide default chat menu button.

    When ``webapp_url`` is set, the blue menu button next to the input field
    opens the user Mini App for every chat without a chat-specific override.
    When unset, it is reset to Telegram's default (the commands list), so
    turning the mini app off cleanly removes the button. The default label is
    the ``DEFAULT_LANGUAGE`` one; ``set_user_menu_button`` localizes per user.
    """
    if webapp_url:
        await bot.set_chat_menu_button(menu_button=_web_app_button(webapp_url, None))
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


async def set_user_menu_button(
    bot: Bot, *, chat_id: int, webapp_url: str, language: str | None
) -> None:
    """Set a chat-specific, localized Mini App menu button for one user."""
    await bot.set_chat_menu_button(
        chat_id=chat_id,
        menu_button=_web_app_button(webapp_url, language),
    )
