from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.state import StateStore
from telegram.i18n import tr
from telegram.handlers import states, keyboards as kb, helpers as h


def build_router(store: StateStore) -> Router:
    router = Router(name="broadcast")

    @router.message(Command("broadcast"))
    async def cmd_broadcast(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await state.clear()
        await state.set_state(states.BroadcastStates.choosing_destinations)
        await state.update_data(selected_chat_ids=[], dest_page=0)
        await h._render_broadcast_destinations(store, message, state, user_id=message.from_user.id, page=0, edit=False)

    @router.callback_query(F.data.startswith("bcpage:"))
    async def cb_broadcast_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        try:
            page = int(query.data.split(":", 1)[1])
        except ValueError:
            await query.answer()
            return

        await query.answer()
        await h._render_broadcast_destinations(
            store,
            query.message,
            state,
            user_id=query.from_user.id,
            page=page,
            edit=True,
        )

    @router.callback_query(F.data.startswith("bc:"))
    async def cb_broadcast_dest_toggle(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        try:
            chat_id = int(parts[1])
        except ValueError:
            await query.answer()
            return

        enabled_token = parts[2]
        if enabled_token not in {"on", "off"}:
            await query.answer()
            return

        all_destinations = await h._list_all_user_destinations(store, query.from_user.id)
        if chat_id not in {destination.chat_id for destination in all_destinations}:
            await query.answer(tr(await h._user_lang(store, query.from_user.id), "broadcast_destination_missing"), show_alert=True)
            return

        ctx = await h.get_broadcast_ctx(state)
        selected_chat_ids = kb._normalize_selected_chat_ids(ctx.selected_chat_ids)
        next_selected_chat_ids = kb._toggle_selected_chat_ids(selected_chat_ids, chat_id, enabled_token == "on")
        page = int(ctx.dest_page or 0)
        await state.update_data(selected_chat_ids=next_selected_chat_ids)
        await query.answer()
        await h._render_broadcast_destinations(
            store,
            query.message,
            state,
            user_id=query.from_user.id,
            page=page,
            edit=True,
        )

    @router.callback_query(F.data == "bcdone")
    async def cb_broadcast_dest_done(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.BroadcastStates.choosing_destinations.state:
            await query.answer()
            return

        lang = await h._user_lang(store, query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, query.from_user.id))
            await state.clear()
            return

        ctx = await h.get_broadcast_ctx(state)
        selected_chat_ids = kb._normalize_selected_chat_ids(ctx.selected_chat_ids)
        valid_chat_ids = {destination.chat_id for destination in await h._list_all_user_destinations(store, query.from_user.id)}
        selected_chat_ids = [chat_id for chat_id in selected_chat_ids if chat_id in valid_chat_ids]
        await state.update_data(selected_chat_ids=selected_chat_ids)
        if not selected_chat_ids:
            await query.answer(tr(lang, "broadcast_choose_one"), show_alert=True)
            return

        await state.update_data(
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
            scheduled_at_utc=None,
            scheduled_local=None,
        )
        await state.set_state(states.BroadcastStates.entering_datetime)
        await query.answer()
        await h._clear_inline_markup(query.message)
        await h._prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "enter_datetime"),
            data=await state.get_data(),
            state_name=states.BroadcastStates.entering_datetime.state,
        )

    return router
