from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.state import StateStore
from telegram.i18n import tr
from telegram.handlers import states, keyboards as kb, helpers as h


async def _render_repeats(store: StateStore, message: Message, *, user_id: int, page: int, edit: bool) -> None:
    lang = await h._user_lang(store, user_id)
    page_size = 5
    page = max(page, 0)
    while True:
        offset = page * page_size
        items = await store.list_user_recurring_summaries(user_id=user_id, offset=offset, limit=page_size + 1)
        if items or page == 0:
            break
        page -= 1

    has_more = len(items) > page_size
    items = items[:page_size]
    if not items:
        text = tr(lang, "repeat_list_empty")
        if edit:
            await message.edit_text(text, reply_markup=None)
        else:
            await message.answer(text, reply_markup=await h._main_menu_for(store, user_id))
        return

    display_tz = await store.get_user_timezone(user_id)
    lines: list[str] = []
    for item in items:
        next_tz = display_tz or item.pattern.timezone
        next_run = tr(lang, "repeat_list_next_missing")
        if item.next_scheduled_at_utc is not None:
            next_run = f"{kb._format_local(item.next_scheduled_at_utc, next_tz)} ({next_tz})"
        lines.append(
            tr(
                lang,
                "repeat_list_item",
                pattern_id=kb._short_id(item.pattern.id),
                where=kb._destination_label(item.destination_title, item.destination_username),
                interval=kb._repeat_interval_label(lang, item.pattern.interval_type),
                next_run=next_run,
                count=kb._repeat_count_label(item.pattern),
            )
        )

    text = tr(lang, "repeat_list_header", lines="\n\n".join(lines))
    reply_markup = kb._repeats_manage_kb(items, page=page, has_more=has_more, lang=lang)
    if edit:
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


def build_router(store: StateStore) -> Router:
    router = Router(name="recurring")

    @router.message(Command("repeat"))
    async def cmd_repeat(message: Message, state: FSMContext) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        tz_name = await store.get_user_timezone(message.from_user.id)
        if not tz_name:
            await message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await state.clear()
        await state.set_state(states.RepeatStates.choosing_interval)
        await message.answer(tr(lang, "repeat_choose_interval"), reply_markup=kb._repeat_interval_kb(lang))

    @router.message(Command("repeats"))
    async def cmd_repeats(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        await _render_repeats(store, message, user_id=message.from_user.id, page=0, edit=False)

    @router.message(Command("repeat_cancel"))
    async def cmd_repeat_cancel(message: Message) -> None:
        await store.ensure_user(message.from_user.id)
        lang = await h._user_lang(store, message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer(tr(lang, "repeat_cancel_usage"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        pattern_ref = parts[1].strip().lower()
        patterns = await store.list_user_recurring(message.from_user.id, include_inactive=True)
        pattern_id = h._resolve_recurring_pattern_id(patterns, pattern_ref)
        if pattern_id is None:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        ok = await store.cancel_recurring_pattern(user_id=message.from_user.id, pattern_id=pattern_id)
        if not ok:
            await message.answer(tr(lang, "repeat_cancel_missing"), reply_markup=await h._main_menu_for(store, message.from_user.id))
            return

        await message.answer(
            tr(lang, "repeat_cancel_ok", pattern_id=kb._short_id(pattern_id)),
            reply_markup=await h._main_menu_for(store, message.from_user.id),
        )

    @router.callback_query(F.data.startswith("rlpage:"))
    async def cb_repeats_page(query: CallbackQuery) -> None:
        page = int(query.data.split(":")[1])
        await query.answer()
        await _render_repeats(store, query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("rstop:"))
    async def cb_repeats_stop(query: CallbackQuery) -> None:
        lang = await h._user_lang(store, query.from_user.id)
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return

        page = int(parts[1])
        pattern_id = parts[2]
        ok = await store.cancel_recurring_pattern(user_id=query.from_user.id, pattern_id=pattern_id)
        await query.answer(
            tr(lang, "repeat_cancel_ok", pattern_id=kb._short_id(pattern_id)) if ok else tr(lang, "repeat_cancel_missing")
        )
        await _render_repeats(store, query.message, user_id=query.from_user.id, page=page, edit=True)

    @router.callback_query(F.data.startswith("rdpage:"))
    async def cb_repeat_dest_page(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.RepeatStates.choosing_destination.state:
            await query.answer()
            return

        page = int(query.data.split(":")[1])
        await h.patch_repeat_ctx(state, dest_page=page)
        await query.answer()
        await h._render_destinations(
            store,
            query.message,
            page=page,
            user_id=query.from_user.id,
            select_prefix="rdsel",
            page_prefix="rdpage",
        )

    @router.callback_query(F.data.startswith("rint:"))
    async def cb_repeat_interval(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.RepeatStates.choosing_interval.state:
            await query.answer()
            return

        lang = await h._user_lang(store, query.from_user.id)
        tz_name = await store.get_user_timezone(query.from_user.id)
        if not tz_name:
            await query.answer()
            await query.message.answer(tr(lang, "timezone_required"), reply_markup=await h._main_menu_for(store, query.from_user.id))
            await state.clear()
            return

        interval_type = query.data.split(":")[1]
        if interval_type == "custom":
            await query.answer(tr(lang, "repeat_custom_unavailable"), show_alert=True)
            return
        if interval_type not in {"daily", "weekly", "weekdays"}:
            await query.answer(tr(lang, "repeat_interval_invalid"), show_alert=True)
            return

        await h.patch_repeat_ctx(
            state,
            interval_type=interval_type,
            selected_date=None,
            calendar_year=None,
            calendar_month=None,
        )
        await state.set_state(states.RepeatStates.entering_datetime)
        await query.answer()
        await h._prompt_for_datetime(
            query.message,
            lang=lang,
            tz_name=tz_name,
            text=tr(lang, "repeat_enter_datetime"),
            data=await state.get_data(),
            state_name=states.RepeatStates.entering_datetime.state,
        )

    @router.callback_query(F.data.startswith("rdsel:"))
    async def cb_repeat_dest_select(query: CallbackQuery, state: FSMContext) -> None:
        if await state.get_state() != states.RepeatStates.choosing_destination.state:
            await query.answer()
            return

        lang = await h._user_lang(store, query.from_user.id)
        ctx = await h.get_repeat_ctx(state)
        scheduled_at_utc = ctx.scheduled_at_utc
        scheduled_local = ctx.scheduled_local
        if not isinstance(scheduled_at_utc, int) or not isinstance(scheduled_local, str):
            await query.answer(tr(lang, "schedule_picker_invalid"), show_alert=True)
            await state.clear()
            return

        chat_id = int(query.data.split(":")[1])
        await h.patch_repeat_ctx(state, chat_id=chat_id)
        await query.answer()
        await h._move_to_post_collection(
            query.message,
            state,
            scheduled_at_utc=scheduled_at_utc,
            scheduled_local=scheduled_local,
            collecting_state=states.RepeatStates.collecting_post,
            lang=lang,
        )

    return router
