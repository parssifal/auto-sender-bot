from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from core.services import admin_broadcast_svc
from core.state import StateStore
from telegram.handlers.states import AdminBroadcastStates
from telegram.i18n import tr

_BROADCAST_PREVIEW_LIMIT = 500


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

    @router.message(Command("admin_broadcast"))
    async def cmd_admin_broadcast(message: Message, state: FSMContext) -> None:
        user = message.from_user
        if user is None or user.id not in admin_set:
            return
        lang = await store.get_user_language(user.id)
        await state.set_state(AdminBroadcastStates.collecting)
        await message.answer(tr(lang, "admin_broadcast_prompt"))

    @router.message(AdminBroadcastStates.collecting)
    async def abc_collect(message: Message, state: FSMContext) -> None:
        lang = await store.get_user_language(message.from_user.id)
        text = (message.text or "").strip()
        if not text:
            await message.answer(tr(lang, "admin_broadcast_empty"))
            return
        entities_json = store.dump_entities(message.entities)
        await state.update_data(bcast_text=text, bcast_entities_json=entities_json)
        await state.set_state(AdminBroadcastStates.confirming)
        count = len(await store.all_user_ids())
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tr(lang, "admin_broadcast_confirm_btn"), callback_data="abc:go"
                    ),
                    InlineKeyboardButton(
                        text=tr(lang, "admin_broadcast_cancel_btn"), callback_data="abc:no"
                    ),
                ]
            ]
        )
        preview = text if len(text) <= _BROADCAST_PREVIEW_LIMIT else text[:_BROADCAST_PREVIEW_LIMIT] + "…"
        await message.answer(
            tr(lang, "admin_broadcast_confirm", count=count, preview=preview),
            reply_markup=keyboard,
        )

    @router.callback_query(AdminBroadcastStates.confirming, F.data == "abc:no")
    async def abc_no(callback: CallbackQuery, state: FSMContext) -> None:
        lang = await store.get_user_language(callback.from_user.id)
        await state.clear()
        await callback.message.edit_text(tr(lang, "admin_broadcast_cancelled"))
        await callback.answer()

    @router.callback_query(AdminBroadcastStates.confirming, F.data == "abc:go")
    async def abc_go(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        lang = await store.get_user_language(callback.from_user.id)
        data = await state.get_data()
        await state.clear()
        await callback.message.edit_text(tr(lang, "admin_broadcast_sending"))
        await callback.answer()
        summary = await admin_broadcast_svc.broadcast_to_all(
            store,
            bot,
            text=data.get("bcast_text", ""),
            entities_json=data.get("bcast_entities_json"),
        )
        await callback.message.answer(tr(lang, "admin_broadcast_report", **summary))

    return router
