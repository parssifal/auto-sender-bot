from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.state import StateStore
from telegram.i18n import key_values, tr
from telegram.handlers import states, helpers as h

_MENU_SCHEDULE_TEXTS = key_values("menu_schedule")


def build_router(store: StateStore) -> Router:
    router = Router(name="schedule")

    @router.message(F.text.in_(_MENU_SCHEDULE_TEXTS))
    @router.message(Command("schedule"))
    async def cmd_schedule(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await state.clear()
        await state.set_state(states.ScheduleStates.choosing_destination)
        await h.patch_schedule_ctx(state, dest_page=0)
        await h._render_destinations(store, message, page=0, user_id=message.from_user.id)

    @router.callback_query(F.data.startswith("sdpage:"))
    async def cb_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.ScheduleStates.choosing_destination.state:
            await query.answer()
            return

        page = int(query.data.split(":")[1])
        await h.patch_schedule_ctx(state, dest_page=page)
        await query.answer()
        await h._render_destinations(store, query.message, page=page, user_id=query.from_user.id)

    @router.callback_query(F.data.startswith("sdsel:"))
    async def cb_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.ScheduleStates.choosing_destination.state:
            await query.answer()
            return

        lang = await h._user_lang(store, query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, query.from_user.id))
            await state.clear()
            return

        chat_id = int(query.data.split(":")[1])
        await h.patch_schedule_ctx(
            state,
            chat_id=chat_id,
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
        )
        await state.set_state(states.ScheduleStates.entering_datetime)
        await query.answer()
        await h._prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "enter_datetime"),
            data=await state.get_data(),
            state_name=states.ScheduleStates.entering_datetime.state,
        )

    return router
