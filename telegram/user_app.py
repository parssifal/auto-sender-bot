from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from core.state import StateStore
from telegram.i18n import tr


def build_user_app_router(*, store: StateStore, webapp_url: str | None) -> Router:
    """Router exposing /app, which opens the user's queue as a Mini App.

    Any user may use it; the Mini App authorizes each request against the
    caller's own posts (per-user, non-admin). When ``webapp_url`` is unset the
    command replies that the app is not configured.
    """
    router = Router(name="user_app")

    @router.message(Command("app"))
    async def cmd_app(message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        await store.ensure_user(user.id)
        lang = await store.get_user_language(user.id)
        if not webapp_url:
            await message.answer(tr(lang, "app_not_configured"))
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tr(lang, "app_open_btn"),
                        web_app=WebAppInfo(url=f"{webapp_url.rstrip('/')}/app"),
                    )
                ]
            ]
        )
        await message.answer(tr(lang, "app_intro"), reply_markup=keyboard)

    return router
