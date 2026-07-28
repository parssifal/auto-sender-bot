from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from core.state import StateStore
from telegram.i18n import tr


def build_admin_router(
    *,
    store: StateStore,
    admin_ids: tuple[int, ...],
    webapp_url: str | None,
) -> Router:
    """Router exposing /admin, which opens the stats dashboard as a Mini App.

    Only users whose id is in ``admin_ids`` get a response; everyone else is
    silently ignored so the command's existence is not advertised.
    """
    router = Router(name="admin")
    admin_set = set(admin_ids)

    @router.message(Command("admin"))
    async def cmd_admin(message: Message) -> None:
        user = message.from_user
        if user is None or user.id not in admin_set:
            return

        lang = await store.get_user_language(user.id)
        if not webapp_url:
            await message.answer(tr(lang, "admin_not_configured"))
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=tr(lang, "admin_open_btn"), web_app=WebAppInfo(url=webapp_url))]
            ]
        )
        await message.answer(tr(lang, "admin_intro"), reply_markup=keyboard)

    return router
